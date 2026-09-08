# -*- coding: utf-8 -*-
"""后端 JSON API（前后端分离改造后的唯一对外层）。

路由（见 LcvSearch/urls.py）：
    GET  /api/search?q=&p=      关键词搜索（高亮 + 分页）
    GET  /api/suggest?s=        搜索框补全
    GET  /api/stats             数据概览（总量/来源分布/热搜词）
    POST /api/crawl/start       触发采集（子进程跑 quotes_ai）
    GET  /api/crawl/status      采集状态 + 日志尾部

所有接口在 ES/Redis/LLM 不可用时返回结构化错误（非 500），前端据此降级展示。
"""
import functools
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

from elasticsearch import Elasticsearch
import redis

from search.crawl_manager import SPIDER_DIR, LOG_DIR, crawl_manager, schedule_manager
from search.live_sources import LIVE_SOURCES, search_live, search_all_sources

# 从 Django settings 读取 ES/Redis 配置（支持环境变量覆盖），不再硬编码
ES_URL = settings.ES_URL
INDEX = settings.ES_INDEX
# 不参与关键词搜索的来源：榜单类走 /api/rankings，评论按视频查看
# （/api/comments），演示站是教学数据（quotes.toscrape.com 练习站）
RANKING_SOURCES = ("douyin_hot", "bilibili_hot", "bilibili_weekly", "douban_movie", "douban_book")
SEARCH_EXCLUDED_SOURCES = RANKING_SOURCES + ("bilibili_comments", "quotes_ai")
COMMENT_FETCH_TIMEOUT = 180   # 按需抓取单次子进程上限（秒）
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


def api_search(request):
    query = request.GET.get("q", "").strip()
    source = request.GET.get("source", "").strip()  # 可选：按来源过滤
    try:
        page = max(int(request.GET.get("p", "1")), 1)
    except ValueError:
        page = 1
    if not query:
        return JsonResponse({"total": 0, "page": 1, "page_nums": 0, "results": []})
    _record_search_keyword(query)

    query_body = {"multi_match": {"query": query,
                                  "fields": ["title^3", "tags^2", "author", "content"],
                                  # 超过 2 个词元的查询要求 80% 词元命中，
                                  # 防止停用词之外的低质单/双词命中刷屏
                                  "minimum_should_match": "2<80%"}}
    # 榜单类来源（热门/每周必看）也支持关键词搜索：关键词匹配 + source 过滤，
    # 不再忽略用户输入的 q（旧实现直接覆盖为 term 查询，导致关键词失效）
    if source in RANKING_SOURCES:
        query_body = {"bool": {"must": query_body,
                                "filter": [{"term": {"source": source}}]}}
    else:
        body = {"must": query_body,
                "must_not": [{"terms": {"source": list(SEARCH_EXCLUDED_SOURCES)}}]}
        if source:
            body["filter"] = [{"term": {"source": source}}]
        query_body = {"bool": body}
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

    try:
        resp = client.search(
            index=INDEX,
            query=query_body,
            from_=(page - 1) * PAGE_SIZE,
            size=PAGE_SIZE,
            highlight={"pre_tags": ['<span class="kw">'], "post_tags": ["</span>"],
                       "fields": {"title": {}, "content": {}}},
        )
    except Exception as e:
        return _es_error(e)

    total = resp["hits"]["total"]["value"]
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

    # 分页 bug 修复：ES 的 total 偶尔会高于实际命中数（估算偏差），
    # 导致出现空页。如果当前页为空且不是第 1 页，自动回退到最后一页。
    if not results and page > 1:
        # 向前查找最后一个有数据的页
        for fallback_page in range(page - 1, 0, -1):
            try:
                resp2 = client.search(
                    index=INDEX, query=query_body,
                    from_=(fallback_page - 1) * PAGE_SIZE, size=PAGE_SIZE,
                    highlight={"pre_tags": ['<span class="kw">'], "post_tags": ["</span>"],
                               "fields": {"title": {}, "content": {}}},
                )
            except Exception:
                continue
            hits2 = resp2["hits"]["hits"]
            if hits2:
                results = []
                for hit in hits2:
                    src = hit["_source"]
                    hl = hit.get("highlight") or {}
                    results.append({
                        "title": "".join(hl["title"]) if hl.get("title") else src.get("title", ""),
                        "content": "".join(hl["content"]) if hl.get("content") else (src.get("content") or "")[:200],
                        "url": src.get("url", ""), "author": src.get("author", ""),
                        "source": src.get("source", ""), "rating": src.get("rating"),
                        "rank": src.get("rank"), "front_image_url": src.get("front_image_url", ""),
                        "create_date": (src.get("create_date") or "")[:10],
                        "praise_nums": src.get("praise_nums"), "view_nums": src.get("view_nums"),
                        "reply_nums": src.get("reply_nums"), "danmaku_nums": src.get("danmaku_nums"),
                    })
                page = fallback_page
                total = (page - 1) * PAGE_SIZE + len(results)
                break
    # 如果当前页结果数少于 PAGE_SIZE 且不是第 1 页，说明这是实际最后一页，修正 page_nums
    page_nums = math.ceil(total / PAGE_SIZE) if total else 0
    if page > 1 and len(results) < PAGE_SIZE:
        page_nums = page

    return JsonResponse({
        "total": total,
        "page": page,
        "page_nums": page_nums,
        "results": results,
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


_STOPWORDS = {"的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
              "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
              "没有", "看", "好", "自己", "这", "那", "这个", "什么", "电影", "视频"}

_KW_CACHE = {"at": 0.0, "data": []}


def _word_freq(limit=40):
    """标题+正文语料的中文词频（jieba），带 300 秒缓存。"""
    if time.time() - _KW_CACHE["at"] < 300:
        return _KW_CACHE["data"]
    try:
        resp = client.search(index=INDEX, query={"match_all": {}}, size=800,
                             _source=["title", "content"])
        texts = ["{} {}".format(h["_source"].get("title", ""),
                                (h["_source"].get("content") or "")[:120])
                 for h in resp["hits"]["hits"]]
        from collections import Counter
        import jieba
        counter = Counter()
        for t in texts:
            for w in jieba.cut(t):
                w = w.strip()
                if len(w) >= 2 and w not in _STOPWORDS and not w.isdigit() \
                        and not w.startswith(("http", "www")):
                    counter[w] += 1
        data = [{"w": w, "c": c} for w, c in counter.most_common(limit)]
        _KW_CACHE["at"] = time.time()
        _KW_CACHE["data"] = data
        return data
    except Exception:
        return []


def api_stats(request):
    data = {"es_ok": True, "total": 0, "by_source": [], "top_keywords": [],
            "keywords": []}
    try:
        resp = client.search(
            index=INDEX,
            query={"match_all": {}},
            size=0,
            aggs={"by_source": {"terms": {"field": "source", "size": 10}}},
        )
        data["total"] = resp["hits"]["total"]["value"]
        data["by_source"] = [{"key": b["key"], "count": b["doc_count"]}
                             for b in resp["aggregations"]["by_source"]["buckets"]]
        data["keywords"] = _word_freq()
    except Exception as e:
        data["es_ok"] = False
        data["es_error"] = str(e)[:200]

    try:
        data["top_keywords"] = redis_cli.zrevrangebyscore(
            "search_keywords_set", "+inf", "-inf", start=0, num=8)
    except Exception:
        pass  # redis 不可用不阻塞概览
    return JsonResponse(data)


def api_rankings(request):
    """榜单数据：B站热门/每周必看、豆瓣电影/图书 Top250。按排名排序的列表，无需关键词。

    榜单是"当前状态"而非"查询对象"——前端点进来看列表，刷新按钮触发对应爬虫。
    """
    source = request.GET.get("source", "bilibili_hot")
    if source not in RANKING_SOURCES:
        return JsonResponse({"error": "不支持的榜单来源"}, status=400)
    try:
        resp = client.search(
            index=INDEX,
            query={"term": {"source": source}},
            sort=[{"rank": "asc"}],
            size=300,  # 豆瓣 Top250 有 250 条，B站榜单通常 < 100 条
        )
    except Exception as e:
        return _es_error(e)

    items = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        items.append({
            "rank": src.get("rank"),
            "title": src.get("title", ""),
            "content": (src.get("content") or "")[:120],
            "url": src.get("url", ""),
            "author": src.get("author", ""),
            "rating": src.get("rating"),
            "front_image_url": src.get("front_image_url", ""),
            "view_nums": src.get("view_nums"),
            "praise_nums": src.get("praise_nums"),
            "danmaku_nums": src.get("danmaku_nums"),
            "reply_nums": src.get("reply_nums"),
            "create_date": (src.get("create_date") or "")[:10],
        })
    return JsonResponse({"source": source, "total": len(items), "items": items})


def api_comments(request):
    """某视频的评论列表（评论按视频归属查看，不参与跨视频搜索）。"""
    bvid = request.GET.get("bvid", "").strip()
    if not bvid:
        return JsonResponse({"error": "缺少参数 bvid"}, status=400)
    try:
        resp = client.search(
            index=INDEX,
            query={"bool": {"must": [
                {"term": {"source": "bilibili_comments"}},
                {"wildcard": {"url_object_id": "{}_*".format(bvid)}},
            ]}},
            sort=[{"praise_nums": {"order": "desc", "missing": "_last"}}],
            size=100,
        )
    except Exception as e:
        return _es_error(e)

    comments = [{"author": h["_source"].get("author", ""),
                 "content": h["_source"].get("content", ""),
                 "likes": h["_source"].get("praise_nums", 0),
                 "date": (h["_source"].get("create_date") or "")[:10]}
                for h in resp["hits"]["hits"]]
    return JsonResponse({"bvid": bvid, "total": len(comments), "comments": comments,
                         "fetching": bvid in _COMMENT_INFLIGHT})


# ---- 评论按需抓取：点"查看评论"时库内无数据则现场抓一次。独立轻量子进程，
# 不占用采集页大任务的互斥锁；同一 bvid 同时只跑一个，其余请求拿到 fetching。
_COMMENT_INFLIGHT = set()
_COMMENT_FETCH_LOCK = threading.Lock()


@require_api_token
@csrf_exempt
@require_http_methods(["POST"])
def api_comments_fetch(request):
    from urllib.parse import urlparse
    bvid = (payload_bvid(request) or "").strip()
    if not bvid.startswith("BV"):
        return JsonResponse({"error": "bvid 需以 BV 开头"}, status=400)
    with _COMMENT_FETCH_LOCK:
        if bvid in _COMMENT_INFLIGHT:
            return JsonResponse({"fetching": True}, status=202)
        _COMMENT_INFLIGHT.add(bvid)

    def _run():
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_path = os.path.join(LOG_DIR, "cmt_fetch_{}.log".format(bvid))
            # 用 with 语句管理文件句柄，确保 subprocess 异常时也能关闭
            with open(log_path, "w") as log:
                subprocess.run(
                    [sys.executable, "-m", "scrapy", "crawl", "bilibili_comments",
                     "-a", "bvid={}".format(bvid), "-a", "pages=2"],
                    cwd=SPIDER_DIR,
                    stdout=log, stderr=subprocess.STDOUT,
                    timeout=COMMENT_FETCH_TIMEOUT)
        except Exception as e:
            logger.error("评论按需抓取失败 %s: %s", bvid, e)
        finally:
            time.sleep(1.2)  # 等 ES refresh，前端随后 GET 即可拿到数据
            with _COMMENT_FETCH_LOCK:
                _COMMENT_INFLIGHT.discard(bvid)

    threading.Thread(target=_run, daemon=True).start()
    return JsonResponse({"fetching": True}, status=202)


def payload_bvid(request):
    try:
        return (json.loads(request.body or b"{}").get("bvid") or "").strip()
    except Exception:
        return ""


@require_api_token
@csrf_exempt
@require_http_methods(["POST"])
def api_crawl_start(request):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "请求体不是合法 JSON"}, status=400)
    spider = payload.get("spider", "bilibili_hot")
    bvid = (payload.get("bvid") or "").strip()
    resume_job = (payload.get("resume_job") or "").strip()
    try:
        pages = min(max(int(payload.get("pages", 2)), 1), 20)
    except (TypeError, ValueError):
        pages = 2
    js = bool(payload.get("js", False))

    result = crawl_manager.start(spider=spider, pages=pages, js=js, bvid=bvid,
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
    """添加定时任务。body: {spider, cron, pages?, js?, bvid?}

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
    bvid = (payload.get("bvid") or "").strip()
    result = schedule_manager.add(spider=spider, cron=cron, pages=pages, js=js, bvid=bvid)
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

    # 返回缓存文件（此时已写入磁盘）
    if os.path.exists(cache_path):
        resp = FileResponse(open(cache_path, "rb"))
    else:
        return JsonResponse({"error": "图片缓存失败"}, status=500)
    resp["Cache-Control"] = "public, max-age={}".format(_IMG_CACHE_MAX_AGE)
    return resp
