# -*- coding: utf-8 -*-
"""后端 JSON API（前后端分离改造后的唯一对外层）。

路由（见 XSearch/urls.py）：
    公开（无需登录）：
        GET  /api/search?q=&p=      关键词搜索（高亮 + 分页）
        GET  /api/suggest?s=        搜索框补全
        GET  /api/stats             数据概览（总量/来源分布/热搜词）
        GET  /api/rankings          榜单 / 新闻列表
        GET  /api/ai/item           AI 条目详情
        GET  /api/img               图片代理
    管理员（X-Admin-Token，require_admin）：
        POST /api/crawl/start       触发采集（子进程跑 Scrapy 爬虫）
        GET  /api/crawl/status      采集状态 + 日志尾部
        POST /api/auth/logout       退出登录（吊销 token）
        「采集管理」的查询/配置类接口（历史/统计/爬虫列表/定时任务）
        与「数据管理」接口（ES 文档浏览/编辑/删除/清理）
    免鉴权：POST /api/auth/login（按 IP 限速，见 _login_locked_for / _login_record_failure）

所有接口在 ES/Redis 不可用时返回结构化错误（非 500），前端据此降级展示。
"""
import functools
import hmac
import json
import logging
import math
import os
import secrets
import threading
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

from elasticsearch import Elasticsearch, NotFoundError
import redis

from search.crawl_manager import crawl_manager, schedule_manager

# 从 Django settings 读取 ES/Redis 配置（支持环境变量覆盖），不再硬编码
ES_URL = settings.ES_URL
INDEX = settings.ES_INDEX
# 不参与关键词搜索的来源：榜单类走 /api/rankings，AI 内容归 /ai 详情页
RANKING_SOURCES = ("douyin_hot", "aihot_hot")
SEARCH_EXCLUDED_SOURCES = RANKING_SOURCES
PAGE_SIZE = 10

# 配置连接超时：ES/Redis 不可用时快速失败，避免每个请求长时间阻塞
client = Elasticsearch(ES_URL, request_timeout=5, retry_on_timeout=False)
redis_cli = redis.StrictRedis.from_url(settings.REDIS_URL, decode_responses=True,
                                        socket_connect_timeout=3, socket_timeout=3)
logger = logging.getLogger(__name__)


def _es_error(e):
    return JsonResponse({"error": "Elasticsearch 不可用: {}".format(e)}, status=503)


def require_api_token(view):
    """可选鉴权：settings.API_TOKEN 非空时要求请求头 X-API-Token 匹配。

    仅用于非浏览器客户端（脚本/内部调用）。浏览器侧的写接口一律走
    require_admin——API_TOKEN 无法安全地打进前端产物，公开仓库里的
    dist 一旦被下载就等于钥匙公开。
    """
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        token = getattr(settings, "API_TOKEN", "")
        if token and request.headers.get("X-API-Token", "") != token:
            return JsonResponse({"error": "无效的 API Token"}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


# ---- 管理员登录（「采集管理」页鉴权）----
# 账号配置在 local_settings.py / 环境变量（见 settings.py，不入 git）。
# 登录成功签发内存态 token（进程重启即失效，需重新登录），前端以
# X-Admin-Token 请求头携带；采集管理的查询/配置类接口均要求该 token。
_ADMIN_TOKEN_TTL = 12 * 3600  # 登录有效期 12 小时（有效期内每次请求滑动续期）
_admin_tokens = {}  # token -> 过期时间戳
_admin_lock = threading.Lock()


def _purge_expired_tokens():
    """清理过期 token（须持 _admin_lock 调用）。"""
    now = time.time()
    for t in [t for t, exp in _admin_tokens.items() if exp <= now]:
        _admin_tokens.pop(t, None)


def require_admin(view):
    """校验 X-Admin-Token：登录态有效则放行并滑动续期，否则返回 401。"""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        token = request.headers.get("X-Admin-Token", "")
        with _admin_lock:
            expiry = _admin_tokens.get(token, 0)
            if token and expiry > time.time():
                _admin_tokens[token] = time.time() + _ADMIN_TOKEN_TTL
                return view(request, *args, **kwargs)
            _purge_expired_tokens()
        return JsonResponse({"error": "未登录或登录已过期", "code": "auth_required"},
                            status=401)
    return wrapped


# ---- 登录限速（按客户端 IP）----
# 与 _admin_tokens 同构，状态在进程内：当前部署为 gunicorn --workers 1（见
# Dockerfile 注释），成立；扩容多 worker 前必须把计数外置到 Redis。
# X-Forwarded-For 可被客户端伪造，这里仍优先取用是因为真实链路是
# frp -> VPS nginx 反代（只看 REMOTE_ADDR 限不到攻击者）；代理不受信任时
# 设环境变量 DJANGO_TRUST_PROXY_HEADER=False 退回 REMOTE_ADDR。
_LOGIN_MAX_FAILS = 5      # 窗口内允许的失败次数
_LOGIN_WINDOW = 300       # 失败计数窗口（秒）
_LOGIN_LOCKOUT = 900      # 超限后锁定时长（秒）
_login_failures = {}      # ip -> (fail_count, window_start, locked_until)


def _client_ip(request):
    if getattr(settings, "TRUST_PROXY_HEADER", True):
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _login_locked_for(ip):
    """返回该 IP 还需等待的秒数，0 表示未被锁定；顺带丢弃过期窗口。"""
    now = time.time()
    with _admin_lock:
        rec = _login_failures.get(ip)
        if not rec:
            return 0
        _count, window_start, locked_until = rec
        if locked_until > now:
            return int(locked_until - now) + 1
        if now - window_start > _LOGIN_WINDOW:
            _login_failures.pop(ip, None)
        return 0


def _login_record_failure(ip):
    """记一次失败；达到阈值则上锁。"""
    now = time.time()
    with _admin_lock:
        count, window_start, _until = _login_failures.get(ip, (0, now, 0))
        if now - window_start > _LOGIN_WINDOW:
            count, window_start = 0, now
        count += 1
        locked_until = now + _LOGIN_LOCKOUT if count >= _LOGIN_MAX_FAILS else 0
        _login_failures[ip] = (count, window_start, locked_until)
        if locked_until:
            logger.warning("登录限速：%s 连续失败 %d 次，锁定 %d 秒",
                           ip or "unknown", count, _LOGIN_LOCKOUT)


def _login_clear(ip):
    with _admin_lock:
        _login_failures.pop(ip, None)


@csrf_exempt
@require_http_methods(["POST"])
def api_admin_login(request):
    """管理员登录。body: {username, password} → {token, username}"""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    ip = _client_ip(request)
    # 先看锁定，再做密码比较：锁定期间不再消耗比较成本，也不给爆破者反馈
    remaining = _login_locked_for(ip)
    if remaining:
        resp = JsonResponse({"error": "失败次数过多，请 {} 秒后重试".format(remaining),
                             "code": "too_many_login_attempts"}, status=429)
        resp["Retry-After"] = str(remaining)
        return resp
    admin_user = getattr(settings, "ADMIN_USERNAME", "")
    admin_pass = getattr(settings, "ADMIN_PASSWORD", "")
    if not admin_user or not admin_pass:
        return JsonResponse(
            {"error": "管理员账号未配置：请创建 XSearch/local_settings.py"
                      "（参考 local_settings.py.example）"},
            status=503)
    # 常数时间比较，避免时序侧信道逐步猜解账号密码
    user_ok = hmac.compare_digest(username.encode("utf-8"), admin_user.encode("utf-8"))
    pass_ok = hmac.compare_digest(password.encode("utf-8"), admin_pass.encode("utf-8"))
    if not (user_ok and pass_ok):
        _login_record_failure(ip)
        return JsonResponse({"error": "用户名或密码错误"}, status=401)
    _login_clear(ip)
    token = secrets.token_hex(32)
    with _admin_lock:
        _admin_tokens[token] = time.time() + _ADMIN_TOKEN_TTL
        _purge_expired_tokens()
    logger.info("管理员「%s」登录成功", admin_user)
    return JsonResponse({"ok": True, "token": token, "username": admin_user,
                         "expires_in": _ADMIN_TOKEN_TTL})


@csrf_exempt
@require_http_methods(["POST"])
def api_admin_logout(request):
    """退出登录：吊销请求头中的 token。"""
    token = request.headers.get("X-Admin-Token", "")
    with _admin_lock:
        _admin_tokens.pop(token, None)
    return JsonResponse({"ok": True})


def _record_search_keyword(query):
    """热搜词统计：zincrby 计数并裁剪到前 100。Redis 不可用时不阻塞搜索。"""
    try:
        redis_cli.zincrby("search_keywords_set", 1, query)
        redis_cli.zremrangebyrank("search_keywords_set", 0, -101)
    except Exception:
        pass


_HIGHLIGHT = {"pre_tags": ['<span class="kw">'], "post_tags": ["</span>"],
              "fields": {"title": {}, "content": {}}}
# 分面聚合：来源分布 + 时间分布（时间桶为累计口径：近30天包含近7天）
_FACET_AGGS = {
    "facet_sources": {"terms": {"field": "source", "size": 20}},
    "facet_days": {"date_range": {"field": "create_date", "ranges": [
        {"key": "7", "from": "now-7d"},
        {"key": "30", "from": "now-30d"},
        {"key": "90", "from": "now-90d"},
    ]}},
}
_SORTS = {"time": [{"create_date": {"order": "desc", "missing": "_last"}}, "_score"],
          "hot": [{"view_nums": {"order": "desc", "missing": "_last"}}, "_score"]}
_DAY_VALUES = ("7", "30", "90")


def _did_you_mean(query):
    """短语建议器：0 结果时给出"您是不是要找"的相近查询词。"""
    try:
        resp = client.search(index=INDEX, suggest={
            "text": query,
            "title_phrase": {"phrase": {"field": "title", "size": 1}},
        })
        options = resp["suggest"]["title_phrase"][0].get("options") or []
        if options:
            return (options[0].get("text") or "").strip() or None
    except Exception:
        pass
    return None


def api_search(request):
    query = request.GET.get("q", "").strip()
    source = request.GET.get("source", "").strip()  # 可选：按来源过滤
    try:
        page = max(int(request.GET.get("p", "1")), 1)
    except ValueError:
        page = 1
    sort_param = request.GET.get("sort", "relevance")   # relevance/time/hot
    days = request.GET.get("days", "").strip()          # 近 N 天过滤（7/30/90）
    if not query:
        return JsonResponse({"total": 0, "page": 1, "page_nums": 0, "results": []})
    _record_search_keyword(query)

    # ---- ES 库内检索 ----
    # 活跃维度的筛选（来源/时间）放 post_filter：分面聚合统计不受自身筛选影响，
    # 每个分面的计数始终是"其他筛选条件下"的分布，供用户直接切换
    post_filters = []
    if source:
        post_filters.append({"term": {"source": source}})
    if days in _DAY_VALUES:
        post_filters.append({"range": {"create_date": {"gte": "now-{}d".format(days)}}})
    sort = _SORTS.get(sort_param)

    def _run_search(match_text, page_num, fuzzy=False):
        mm = {"query": match_text,
              "fields": ["title^3", "tags^2", "author", "content"],
              # 超过 2 个词元的查询要求 80% 词元命中，防止低质命中刷屏
              "minimum_should_match": "2<80%"}
        if fuzzy:
            # 模糊重搜：编辑距离容错（如"猪申克"→"肖申克"），
            # 并放宽词元命中率让部分错词不拖垮整个查询
            mm["fuzziness"] = "AUTO"
            mm["minimum_should_match"] = "2<50%"
        body = {
            "index": INDEX,
            "query": {"bool": {
                "must": {"multi_match": mm},
                "must_not": [{"terms": {"source": list(SEARCH_EXCLUDED_SOURCES)}}],
            }},
            "from_": (page_num - 1) * PAGE_SIZE,
            "size": PAGE_SIZE,
            "highlight": _HIGHLIGHT,
            "aggs": _FACET_AGGS,
        }
        if post_filters:
            body["post_filter"] = {"bool": {"must": post_filters}}
        if sort:
            body["sort"] = sort
        return client.search(**body)

    def _extract(resp):
        results = []
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            hl = hit.get("highlight") or {}
            results.append({
                "title": "".join(hl["title"]) if hl.get("title") else src.get("title", ""),
                "content": "".join(hl["content"]) if hl.get("content") else (src.get("content") or "")[:200],
                "url": src.get("url", ""),
                "author": src.get("author", ""),
                "source": src.get("source", ""),
                "rating": src.get("rating"),
                "rank": src.get("rank"),
                "front_image_url": src.get("front_image_url", ""),
                "create_date": (src.get("create_date") or "")[:10],
                "praise_nums": src.get("praise_nums"),
                "view_nums": src.get("view_nums"),
                "reply_nums": src.get("reply_nums"),
                "danmaku_nums": src.get("danmaku_nums"),
            })
        aggs = resp.get("aggregations") or {}
        facets = {
            "sources": [{"key": b["key"], "count": b["doc_count"]}
                        for b in (aggs.get("facet_sources") or {}).get("buckets", [])],
            "days": [{"key": b["key"], "count": b["doc_count"]}
                     for b in (aggs.get("facet_days") or {}).get("buckets", [])],
        }
        return resp["hits"]["total"]["value"], results, facets

    try:
        resp = _run_search(query, page)
    except Exception as e:
        return _es_error(e)
    total, results, facets = _extract(resp)

    # 容错纠错（两级）：0 结果时先用短语建议器找相近词重搜；
    # 仍无结果则用编辑距离模糊匹配重搜（fuzziness AUTO）
    did_you_mean = None
    corrected = None
    fuzzy_match = False
    if total == 0 and page == 1:
        did_you_mean = _did_you_mean(query)
        if did_you_mean and did_you_mean != query:
            try:
                total2, results2, facets2 = _extract(_run_search(did_you_mean, 1))
                if results2:
                    corrected, total, results, facets = did_you_mean, total2, results2, facets2
            except Exception:
                pass
        if not results:
            try:
                total2, results2, facets2 = _extract(_run_search(query, 1, fuzzy=True))
                if results2:
                    fuzzy_match, total, results, facets = True, total2, results2, facets2
            except Exception:
                pass

    # 分页 bug 修复：ES 的 total 偶尔会高于实际命中数（估算偏差），
    # 导致出现空页。如果当前页为空且不是第 1 页，自动回退到最后一页。
    if not results and page > 1:
        # 向前查找最后一个有数据的页
        for fallback_page in range(page - 1, 0, -1):
            try:
                resp2 = _run_search(query, fallback_page)
            except Exception:
                continue
            hits2 = resp2["hits"]["hits"]
            if hits2:
                _, results, _ = _extract(resp2)
                page = fallback_page
                total = (page - 1) * PAGE_SIZE + len(results)
                break
    # 如果当前页结果数少于 PAGE_SIZE 且不是第 1 页，说明这是实际最后一页，修正 page_nums
    page_nums = math.ceil(total / PAGE_SIZE) if total else 0
    if page > 1 and len(results) < PAGE_SIZE:
        page_nums = page

    # 空结果兜底：附上全站热搜词，前端展示"换个词试试"引导
    suggestions = []
    if not results:
        try:
            suggestions = redis_cli.zrevrangebyscore(
                "search_keywords_set", "+inf", "-inf", start=0, num=6)
        except Exception:
            pass

    return JsonResponse({
        "total": total,
        "page": page,
        "page_nums": page_nums,
        "results": results,
        "facets": facets,
        "did_you_mean": did_you_mean,
        "corrected": corrected,
        "fuzzy": fuzzy_match,
        "suggestions": suggestions,
    })


def api_suggest(request):
    prefix = request.GET.get("s", "").strip()
    if not prefix:
        return JsonResponse([], safe=False)
    try:
        # elasticsearch-dsl 8.x 已移除 execute_suggest，直接用原生客户端
        resp = client.search(
            index=INDEX,
            suggest={"my_suggest": {
                "prefix": prefix,
                "completion": {"field": "suggest",
                               "fuzzy": {"fuzziness": 2}, "size": 10},
            }},
        )
        options = resp["suggest"]["my_suggest"][0]["options"]
        # 电影/图书同名时建议会重复，保序去重
        titles = list(dict.fromkeys(
            o["_source"].get("title", "") for o in options if o.get("_source")
            and o["_source"].get("source") not in SEARCH_EXCLUDED_SOURCES))
        return JsonResponse(titles, safe=False)
    except Exception:
        return JsonResponse([], safe=False)


def api_stats(request):
    data = {"es_ok": True, "total": 0, "by_source": [], "top_keywords": []}
    try:
        resp = client.search(
            index=INDEX,
            query={"match_all": {}},
            size=0,
            # 来源已超过 10 个（新闻源加入后共 14+），聚合 size 放宽
            aggs={"by_source": {"terms": {"field": "source", "size": 20}}},
        )
        data["total"] = resp["hits"]["total"]["value"]
        data["by_source"] = [{"key": b["key"], "count": b["doc_count"]}
                             for b in resp["aggregations"]["by_source"]["buckets"]]
    except Exception as e:
        data["es_ok"] = False
        data["es_error"] = str(e)[:200]

    try:
        data["top_keywords"] = redis_cli.zrevrangebyscore(
            "search_keywords_set", "+inf", "-inf", start=0, num=8)
    except Exception:
        pass  # redis 不可用不阻塞概览
    return JsonResponse(data)


# 新闻来源：news_rss 爬虫入库的 3 个 RSS 源（AIHOT 内容归 /ai 页，不混入新闻）
# 人民网（news_people）已停用：其 RSS 自 2025-06 起不再更新，见 spiders/news_rss.py 说明。
# 已入库的 news_people 文档仍可通过 /api/search 检索到，故保留在前端来源标签映射里。
NEWS_SOURCES = ("news_chinanews", "news_ithome", "news_solidot")
# AI 来源：AIHOT 精选动态 / 日报 / 热点榜（独立 AI 导航页使用）
AI_SOURCES = ("aihot_news", "aihot_daily", "aihot_hot")


@require_http_methods(["GET"])
def api_ai_item(request):
    """AI 条目详情：按标题在本地 ES 中检索完整文档（AI 精选/日报/热点榜）。

    供 /ai/detail 详情页使用——标题相关度排序，返回完整正文（不截断）。
    """
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"error": "缺少参数 q"}, status=400)
    try:
        resp = client.search(
            index=INDEX,
            query={"bool": {
                "must": {"multi_match": {"query": q, "fields": ["title^3", "content"]}},
                "filter": [{"terms": {"source": list(AI_SOURCES)}}],
            }},
            size=5,
        )
    except Exception as e:
        return _es_error(e)
    items = [{
        "title": h["_source"].get("title", ""),
        "content": h["_source"].get("content", ""),
        "author": h["_source"].get("author", ""),
        "source": h["_source"].get("source", ""),
        "rating": h["_source"].get("rating"),
        "url": h["_source"].get("url", ""),
        "create_date": (h["_source"].get("create_date") or "")[:10],
    } for h in resp["hits"]["hits"]]
    return JsonResponse({"q": q, "total": len(items), "items": items})


def _list_item(src, keep_content=False):
    """榜单/新闻列表条目的统一字段映射。

    keep_content=True 时保留全文（AI 日报/热点榜页需要展示综述与日报正文），
    否则截断为 150 字摘要。
    """
    return {
        "rank": src.get("rank"),
        "title": src.get("title", ""),
        "content": (src.get("content") or "")[:5000 if keep_content else 150],
        "url": src.get("url", ""),
        "author": src.get("author", ""),
        "source": src.get("source", ""),
        "rating": src.get("rating"),
        "front_image_url": src.get("front_image_url", ""),
        "view_nums": src.get("view_nums"),
        "praise_nums": src.get("praise_nums"),
        "danmaku_nums": src.get("danmaku_nums"),
        "reply_nums": src.get("reply_nums"),
        "create_date": (src.get("create_date") or "")[:10],
    }


def api_rankings(request):
    """榜单/新闻列表数据，无需关键词。

    - 榜单（B站/豆瓣/抖音）：按 rank 升序，一次性拉全（百条级）
    - 新闻（source=news 或具体新闻源）：按发布时间倒序 + 分页
      （语料随定时任务持续增长，不能一次拉全）
    """
    source = request.GET.get("source", "aihot_hot")
    try:
        page = max(int(request.GET.get("p", "1")), 1)
    except ValueError:
        page = 1
    if (source not in RANKING_SOURCES and source != "news"
            and source not in NEWS_SOURCES and source not in AI_SOURCES):
        return JsonResponse({"error": "不支持的榜单来源"}, status=400)

    # 分页时间倒序分支：新闻 3 源聚合 + 各源 + AI 日报（aihot_hot 属榜单类，走下方 rank 分支）
    if source == "news" or source in NEWS_SOURCES or source == "aihot_daily":
        page_size = 20
        # 新闻列表 60 秒缓存：页面轮询/翻页密集，且语料分钟级变化足够
        cache_key = "rankings_news:{}:p{}".format(source, page)
        try:
            raw = redis_cli.get(cache_key)
            if raw:
                return JsonResponse(json.loads(raw))
        except Exception:
            pass
        if source == "news":
            query = {"terms": {"source": list(NEWS_SOURCES)}}
        else:
            query = {"term": {"source": source}}
        try:
            resp = client.search(
                index=INDEX,
                query=query,
                sort=[{"create_date": {"order": "desc", "missing": "_last"}}],
                from_=(page - 1) * page_size,
                size=page_size,
            )
        except Exception as e:
            return _es_error(e)
        total = resp["hits"]["total"]["value"]
        # AI 日报需要完整正文（正文即日报内容）
        keep = source in AI_SOURCES
        payload = {
            "source": source, "total": total, "page": page,
            "page_nums": math.ceil(total / page_size) if total else 0,
            "items": [_list_item(h["_source"], keep_content=keep) for h in resp["hits"]["hits"]],
        }
        try:
            redis_cli.setex(cache_key, 60, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass
        return JsonResponse(payload)

    try:
        resp = client.search(
            index=INDEX,
            query={"term": {"source": source}},
            sort=[{"rank": "asc"}],
            size=300,  # 榜单为"当前状态"全集（抖音 50 条 / AI 热点 10 条）
        )
    except Exception as e:
        return _es_error(e)

    # AI 热点榜需要完整 AI 综述
    items = [_list_item(h["_source"], keep_content=source in AI_SOURCES)
             for h in resp["hits"]["hits"]]
    return JsonResponse({"source": source, "total": len(items), "items": items})


@require_admin
@csrf_exempt
@require_http_methods(["POST"])
def api_crawl_start(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    spider = payload.get("spider", "douyin_hot")
    resume_job = (payload.get("resume_job") or "").strip()
    try:
        pages = min(max(int(payload.get("pages", 2)), 1), 20)
    except (TypeError, ValueError):
        pages = 2
    js = bool(payload.get("js", False))

    result = crawl_manager.start(spider=spider, pages=pages, js=js,
                                  resume_job=resume_job)
    status_code = 202 if result.get("started") else 409
    return JsonResponse(result, status=status_code)


@require_admin
def api_crawl_resumable(request):
    """列出可恢复的爬虫任务（JOBDIR 列表）。"""
    return JsonResponse({"jobs": crawl_manager.list_resumable_jobs()})


@require_admin
def api_crawl_status(request):
    """采集状态 + 日志尾部（含服务器绝对路径，仅限管理员）。"""
    return JsonResponse(crawl_manager.status())


@require_admin
def api_crawl_history(request):
    """爬虫任务历史记录。"""
    try:
        limit = min(max(int(request.GET.get("limit", "50")), 1), 200)
    except ValueError:
        limit = 50
    return JsonResponse({"history": crawl_manager.history(limit=limit)})


@require_admin
def api_crawl_stats(request):
    """爬虫任务统计：按天/按爬虫统计、成功率。"""
    return JsonResponse(crawl_manager.stats())


@require_admin
def api_crawl_spiders(request):
    return JsonResponse({"spiders": crawl_manager.list_spiders()})


# ---- 爬虫定时任务 API（仅管理员） ----
@require_admin
def api_schedule_list(request):
    """列出所有定时任务 + 最近触发历史。"""
    return JsonResponse(schedule_manager.list())


@require_admin
@require_api_token
@csrf_exempt
@require_http_methods(["POST"])
def api_schedule_add(request):
    """添加定时任务。body: {spider, cron, pages?, js?}

    cron 格式：分 时 日 月 周（如 "0 8 * * *" = 每天8点，"*/30 * * * *" = 每30分钟）
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    spider = (payload.get("spider") or "").strip()
    cron = (payload.get("cron") or "").strip()
    if not spider or not cron:
        return JsonResponse({"error": "缺少 spider 或 cron 参数"}, status=400)
    try:
        pages = min(max(int(payload.get("pages", 2)), 1), 20)
    except (TypeError, ValueError):
        pages = 2
    js = bool(payload.get("js", False))
    result = schedule_manager.add(spider=spider, cron=cron, pages=pages, js=js)
    status_code = 201 if result.get("ok") else 400
    return JsonResponse(result, status=status_code)


@require_admin
@require_api_token
@csrf_exempt
@require_http_methods(["POST"])
def api_schedule_remove(request):
    """删除定时任务。body: {job_id}"""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    job_id = (payload.get("job_id") or "").strip()
    if not job_id:
        return JsonResponse({"error": "缺少 job_id"}, status=400)
    return JsonResponse(schedule_manager.remove(job_id))


@require_admin
@require_api_token
@csrf_exempt
@require_http_methods(["POST"])
def api_schedule_update(request):
    """更新定时任务。body: {job_id, cron?, spider?, pages?, js?}

    未传的字段保持原值；cron 变更时做与添加时相同的三级校验。
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    job_id = (payload.get("job_id") or "").strip()
    if not job_id:
        return JsonResponse({"error": "缺少 job_id"}, status=400)
    result = schedule_manager.update(
        job_id,
        cron=payload.get("cron"),
        spider=payload.get("spider"),
        pages=payload.get("pages"),
        js=payload.get("js"),
    )
    return JsonResponse(result, status=200 if result.get("ok") else 400)


@require_admin
@require_api_token
@csrf_exempt
@require_http_methods(["POST"])
def api_schedule_toggle(request):
    """启用/禁用定时任务。body: {job_id, enabled}"""
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    job_id = (payload.get("job_id") or "").strip()
    enabled = bool(payload.get("enabled", True))
    if not job_id:
        return JsonResponse({"error": "缺少 job_id"}, status=400)
    return JsonResponse(schedule_manager.toggle(job_id, enabled))


# ---- 数据管理（ES quotes 索引，仅管理员）----
# 管理视角与 /api/search 的差异：不排除榜单来源（全量数据）、返回完整字段
# （列表 content 截断，全文走详情）、可写可删。
DB_DOC_EDITABLE_FIELDS = ("title", "content", "tags", "author")
DB_PAGE_SIZE = 20


def _valid_doc_id(doc_id):
    """ES _id 基本合法性：非空、无空白字符、长度受限。"""
    return bool(doc_id) and len(doc_id) <= 128 and not any(c.isspace() for c in doc_id)


@require_admin
@require_http_methods(["GET"])
def api_db_overview(request):
    """索引概览：文档总数、磁盘大小、各来源文档数分布。"""
    try:
        stats = client.indices.stats(index=INDEX)
        primaries = stats["_all"]["primaries"]
        resp = client.search(index=INDEX, query={"match_all": {}}, size=0,
                             aggs={"by_source": {"terms": {"field": "source", "size": 30}}})
    except Exception as e:
        return _es_error(e)
    return JsonResponse({
        "total": primaries["docs"]["count"],
        "size_bytes": primaries["store"]["size_in_bytes"],
        "by_source": [{"key": b["key"], "count": b["doc_count"]}
                      for b in resp["aggregations"]["by_source"]["buckets"]],
    })


@require_admin
@require_http_methods(["GET"])
def api_db_docs(request):
    """文档分页浏览：来源筛选 + 关键词检索，按采集时间倒序。"""
    source = request.GET.get("source", "").strip()
    q = request.GET.get("q", "").strip()
    try:
        page = max(int(request.GET.get("p", "1")), 1)
    except ValueError:
        page = 1

    must = [{"multi_match": {"query": q, "fields": ["title^2", "content"]}}] if q \
        else [{"match_all": {}}]
    try:
        resp = client.search(
            index=INDEX,
            query={"bool": {"must": must,
                            "filter": [{"term": {"source": source}}] if source else []}},
            sort=[{"crawled_at": {"order": "desc", "missing": "_last"}}],
            from_=(page - 1) * DB_PAGE_SIZE,
            size=DB_PAGE_SIZE,
        )
    except Exception as e:
        return _es_error(e)

    total = resp["hits"]["total"]["value"]
    items = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        item = {k: src.get(k) for k in ("title", "source", "author", "url",
                                        "rating", "rank", "view_nums",
                                        "praise_nums", "create_date")}
        item["id"] = hit["_id"]
        item["content"] = (src.get("content") or "")[:300]
        item["crawled_at"] = (str(src.get("crawled_at") or "")).replace("T", " ")[:19]
        items.append(item)
    return JsonResponse({
        "total": total, "page": page, "page_size": DB_PAGE_SIZE,
        "page_nums": math.ceil(total / DB_PAGE_SIZE) if total else 0,
        "items": items,
    })


def _db_doc_detail(doc_id):
    try:
        resp = client.get(index=INDEX, id=doc_id)
    except NotFoundError:
        return JsonResponse({"error": "文档不存在"}, status=404)
    except Exception as e:
        return _es_error(e)
    data = dict(resp["_source"])
    data["id"] = resp["_id"]
    # 与列表接口保持同一展示格式（ISO 的 T 分隔换空格）
    data["crawled_at"] = (str(data.get("crawled_at") or "")).replace("T", " ")[:19]
    return JsonResponse(data)


def _db_doc_update(request, doc_id):
    """编辑文档：白名单字段 partial update（不覆盖其他字段）。

    suggest 补全字段 input=[title, author]，因此标题或作者任一变化都要重建。
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    fields = {k: payload[k] for k in DB_DOC_EDITABLE_FIELDS if k in payload}
    if not fields:
        return JsonResponse(
            {"error": "没有可更新的字段（允许：%s）" % "、".join(DB_DOC_EDITABLE_FIELDS)},
            status=400)
    if "title" in fields and not str(fields["title"]).strip():
        return JsonResponse({"error": "标题不能为空"}, status=400)
    if "title" in fields or "author" in fields:
        try:
            current = client.get(index=INDEX, id=doc_id, _source=["title", "author"])
        except NotFoundError:
            return JsonResponse({"error": "文档不存在"}, status=404)
        except Exception as e:
            return _es_error(e)
        title = str(fields.get("title", current["_source"].get("title") or ""))
        author = str(fields.get("author", current["_source"].get("author") or ""))
        fields["suggest"] = {"input": [title, author]}
    try:
        client.update(index=INDEX, id=doc_id, doc=fields)
    except NotFoundError:
        return JsonResponse({"error": "文档不存在"}, status=404)
    except Exception as e:
        return _es_error(e)
    return JsonResponse({"ok": True})


def _db_doc_delete(doc_id):
    try:
        client.delete(index=INDEX, id=doc_id)
    except NotFoundError:
        return JsonResponse({"error": "文档不存在"}, status=404)
    except Exception as e:
        return _es_error(e)
    return JsonResponse({"ok": True})


@require_admin
@csrf_exempt
def api_db_doc(request, doc_id):
    """单文档管理：GET 详情 / PUT 编辑 / DELETE 删除。

    三种方法共用一个视图：Django 命中第一个路径即调用视图、不按 HTTP 方法
    回退匹配，拆成同路径三个视图会让 PUT/DELETE 被 GET 视图直接 405。
    """
    if not _valid_doc_id(doc_id):
        return JsonResponse({"error": "非法文档 ID"}, status=400)
    if request.method == "GET":
        return _db_doc_detail(doc_id)
    if request.method == "PUT":
        return _db_doc_update(request, doc_id)
    if request.method == "DELETE":
        return _db_doc_delete(doc_id)
    return JsonResponse({"error": "不支持的方法"}, status=405)


def _purge_rankings_cache(source):
    """按来源精确清理榜单 Redis 缓存。

    Redis db0 与其他项目共享，只允许 scan_iter 精确匹配 XSearch 自己的键。
    """
    patterns = ["rankings_news:{}:p*".format(source)]
    if source in NEWS_SOURCES:
        patterns.append("rankings_news:news:p*")  # 新闻聚合列表同样包含该来源
    try:
        for pattern in patterns:
            keys = list(redis_cli.scan_iter(match=pattern, count=100))
            if keys:
                redis_cli.delete(*keys)
    except Exception:
        pass  # 清缓存失败不阻塞（60s TTL 自然过期兜底）


@require_admin
@csrf_exempt
@require_http_methods(["POST"])
def api_db_purge(request):
    """按来源批量清理文档（危险操作）。

    body: {source, confirm} —— confirm 必须与 source 逐字一致，防误触。

    ES 的 delete_by_query 只能命中"已刷新"的文档：爬虫刚写入、还在刷新窗口
    （默认 1s）内的数据会躲过删除。因此先显式 refresh，删完再校验残余，
    有残留（期间爬虫又写入）则补删一轮，保证"清空该来源"的承诺。
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    source = (payload.get("source") or "").strip()
    confirm = (payload.get("confirm") or "").strip()
    if not source:
        return JsonResponse({"error": "缺少 source"}, status=400)
    if confirm != source:
        return JsonResponse({"error": "confirm 与 source 不一致，拒绝执行"}, status=400)
    term = {"term": {"source": source}}
    try:
        client.indices.refresh(index=INDEX)
        resp = client.delete_by_query(index=INDEX, query=term, refresh=True)
        deleted = resp.get("deleted", 0)
        if client.count(index=INDEX, query=term)["count"]:
            resp = client.delete_by_query(index=INDEX, query=term, refresh=True)
            deleted += resp.get("deleted", 0)
    except Exception as e:
        return _es_error(e)
    if deleted:
        _purge_rankings_cache(source)
    logger.info("数据管理：来源「%s」清理 %d 条文档", source, deleted)
    return JsonResponse({"ok": True, "deleted": deleted})


# 图片代理：豆瓣/B站图床均有防盗链校验，由后端携带站内 Referer 拉取
_IMG_HOSTS = ("img1.doubanio.com", "img2.doubanio.com", "img3.doubanio.com",
              "img9.doubanio.com", "i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com")
_IMG_REFERER = {"doubanio.com": "https://movie.douban.com/",
                "hdslb.com": "https://www.bilibili.com/"}

# 图片本地缓存目录：豆瓣 Top250 等基本不变的图片缓存到磁盘，避免每次请求都回源
_IMG_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache", "images")
_IMG_CACHE_MAX_AGE = 30 * 24 * 3600  # 缓存 30 天
_IMG_CACHE_MAX_FILES = 2000          # 文件数上限，超出按 mtime 淘汰最旧的一半


def _img_cache_cleanup():
    """图片缓存超过文件数上限时淘汰最旧的一半，防止磁盘无限增长。"""
    try:
        files = [os.path.join(_IMG_CACHE_DIR, f) for f in os.listdir(_IMG_CACHE_DIR)]
        files = [f for f in files if os.path.isfile(f)]
        if len(files) <= _IMG_CACHE_MAX_FILES:
            return
        files.sort(key=os.path.getmtime)
        for f in files[:len(files) // 2]:
            try:
                os.remove(f)
            except OSError:
                pass
        logger.info("图片缓存淘汰完成，剩余约 %d 个文件", _IMG_CACHE_MAX_FILES // 2)
    except Exception:
        pass


def _img_cache_path(url):
    """根据 URL 生成本地缓存文件路径（MD5 + 原扩展名）。"""
    import hashlib
    from urllib.parse import urlparse
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(_IMG_CACHE_DIR, h + ext)


def api_img(request):
    """图片代理 + 本地磁盘缓存。

    白名单校验 → 本地缓存命中则直接返回 → 未命中则回源下载并缓存。
    豆瓣 Top250 等长期不变的图片首次加载后永久缓存，后续请求零回源。
    """
    from urllib.parse import urlparse
    import requests as _requests
    from django.http import FileResponse

    url = request.GET.get("u", "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    # 注意边界：host.endswith("doubanio.com") 会被 evil-doubanio.com 这类
    # 可注册域名绕过（SSRF），必须按"."边界精确匹配
    allowed = any(host == h or host.endswith("." + h) for h in _IMG_HOSTS)
    # 额外校验：URL 中不得携带用户名/密码，防止 http://evil@doubanio.com 类混淆
    if not url.startswith("http") or not allowed or parsed.username or parsed.password:
        return JsonResponse({"error": "非白名单图床或 URL 格式非法"}, status=400)

    # ---- 本地缓存命中：直接返回磁盘文件，零回源 ----
    cache_path = _img_cache_path(url)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        resp = FileResponse(open(cache_path, "rb"))
        resp["Cache-Control"] = "public, max-age={}".format(_IMG_CACHE_MAX_AGE)
        return resp

    # ---- 缓存未命中：回源下载 ----
    referer = next((r for h, r in _IMG_REFERER.items()
                    if host == h or host.endswith("." + h)), "")
    try:
        upstream = _requests.get(url, headers={"Referer": referer,
                                               "User-Agent": "Mozilla/5.0"},
                                 timeout=10, stream=True,
                                 allow_redirects=False)
    except Exception:
        return JsonResponse({"error": "图床不可达"}, status=502)
    if upstream.status_code != 200:
        return JsonResponse({"error": "图片拉取失败"}, status=upstream.status_code)
    declared = int(upstream.headers.get("Content-Length") or 0)
    if declared > 20 * 1024 * 1024:  # 单图 20MB 上限
        upstream.close()
        return JsonResponse({"error": "图片过大"}, status=413)

    # 写入临时文件，完成后原子重命名为缓存文件（避免并发写入损坏）
    os.makedirs(_IMG_CACHE_DIR, exist_ok=True)
    tmp_path = cache_path + ".tmp.{}.{}".format(os.getpid(), int(time.time() * 1000))
    try:
        with open(tmp_path, "wb") as f:
            for chunk in upstream.iter_content(8192):
                f.write(chunk)
        os.rename(tmp_path, cache_path)
    except Exception:
        # 缓存写入失败不影响返回，清理临时文件
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    finally:
        upstream.close()

    # 低频触发缓存容量清理（1% 的回源请求），摊薄目录扫描成本
    import random
    if random.random() < 0.01:
        _img_cache_cleanup()

    # 返回缓存文件（此时已写入磁盘）
    if os.path.exists(cache_path):
        resp = FileResponse(open(cache_path, "rb"))
    else:
        return JsonResponse({"error": "图片缓存失败"}, status=500)
    resp["Cache-Control"] = "public, max-age={}".format(_IMG_CACHE_MAX_AGE)
    return resp
