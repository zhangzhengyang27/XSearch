# -*- coding: utf-8 -*-
"""后端 JSON API（前后端分离改造后的唯一对外层）。

路由（见 XSearch/urls.py）：
    GET  /api/search?q=&p=      关键词搜索（高亮 + 分页）
    GET  /api/suggest?s=        搜索框补全
    GET  /api/stats             数据概览（总量/来源分布/热搜词）
    POST /api/crawl/start       触发采集（子进程跑 Scrapy 爬虫）
    GET  /api/crawl/status      采集状态 + 日志尾部

所有接口在 ES/Redis/LLM 不可用时返回结构化错误（非 500），前端据此降级展示。
"""
import functools
import json
import logging
import math
import os
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

from elasticsearch import Elasticsearch
import redis

from search.crawl_manager import crawl_manager, schedule_manager
from search.live_sources import LIVE_SOURCES, search_live, search_all_sources

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

    本机开发态默认不启用（Token 留空即开放）；公网部署时设置 API_TOKEN
    即可保护写操作与 AI 问答（LLM 调用有成本）。
    """
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        token = getattr(settings, "API_TOKEN", "")
        if token and request.headers.get("X-API-Token", "") != token:
            return JsonResponse({"error": "无效的 API Token"}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


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

    if source in LIVE_SOURCES:
        # 实时联邦搜索：现场调平台接口，结果同时后台写入 ES 累积语料
        try:
            items = search_live(source, query, page)
        except Exception as e:
            return JsonResponse({"error": "{} 搜索失败: {}".format(source, e)}, status=502)
        results = [{k: v for k, v in it.items() if k != "create_date"} | {
            "create_date": (str(it["create_date"])[:10] if it.get("create_date") else "")}
            for it in items]
        return JsonResponse({"total": len(results), "page": page, "page_nums": 1,
                             "results": results, "live": True})

    if source == "all":
        # 多源聚合搜索：并发查所有实时源，合并后按相关度统一排序
        try:
            data = search_all_sources(query, page)
        except Exception as e:
            return JsonResponse({"error": "多源搜索失败: {}".format(e)}, status=502)
        results = [{k: v for k, v in it.items() if k not in ("create_date", "_live_source")} | {
            "create_date": (str(it["create_date"])[:10] if it.get("create_date") else "")}
            for it in data["results"]]
        return JsonResponse({"total": data["total"], "page": data["page"],
                             "page_nums": data["page_nums"], "results": results,
                             "by_source": data["by_source"], "errors": data.get("errors", {}),
                             "live": True})

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


# 新闻来源：news_rss 爬虫入库的 4 个 RSS 源（AIHOT 内容归 /ai 页，不混入新闻）
NEWS_SOURCES = ("news_people", "news_chinanews", "news_ithome", "news_solidot")
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

    # 分页时间倒序分支：新闻 4 源聚合 + 各源 + AI 日报（aihot_hot 属榜单类，走下方 rank 分支）
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


@require_api_token
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


def api_crawl_resumable(request):
    """列出可恢复的爬虫任务（JOBDIR 列表）。"""
    return JsonResponse({"jobs": crawl_manager.list_resumable_jobs()})


def api_crawl_status(request):
    return JsonResponse(crawl_manager.status())


def api_crawl_history(request):
    """爬虫任务历史记录。"""
    try:
        limit = min(max(int(request.GET.get("limit", "50")), 1), 200)
    except ValueError:
        limit = 50
    return JsonResponse({"history": crawl_manager.history(limit=limit)})


def api_crawl_stats(request):
    """爬虫任务统计：按天/按爬虫统计、成功率。"""
    return JsonResponse(crawl_manager.stats())


def api_crawl_spiders(request):
    return JsonResponse({"spiders": crawl_manager.list_spiders()})


# ---- 爬虫定时任务 API ----
def api_schedule_list(request):
    """列出所有定时任务 + 最近触发历史。"""
    return JsonResponse(schedule_manager.list())


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
