# -*- coding: utf-8 -*-
"""实时联邦搜索：把外部平台的搜索结果接入本站。

与批量爬虫的分工：
    - 批量爬虫（news_rss 等）：定时/手动采集"榜单/全集"型数据，进 ES；
    - 实时联邦搜索（本模块）：用户搜索时选了外部平台，现场调该平台搜索接口，
      结果直接返回给前端，同时后台写入 ES 累积语料（供搜索复用）。

统一返回字段与爬虫 item 一致（title/content/author/url/...），
因此复用 pipelines.index_item 入库与前端卡片模板。
"""
import hashlib
import json
import re
import threading
import time

import requests

# Redis 缓存配置：实时搜索结果短期缓存，减少对目标站的请求
CACHE_TTL = 300  # 5 分钟
CACHE_PREFIX = "live_search:"

_redis_cli = None


def _get_redis():
    """延迟获取 Redis 客户端，未配置时返回 None（缓存降级为不缓存）。"""
    global _redis_cli
    if _redis_cli is not None:
        return _redis_cli
    try:
        import redis
        from django.conf import settings
        _redis_cli = redis.StrictRedis.from_url(
            settings.REDIS_URL, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2)
        return _redis_cli
    except Exception:
        return None


def _cache_key(source, query, page=1):
    """生成缓存 key：用哈希避免 key 过长。"""
    raw = "{}:{}:{}".format(source, query.lower().strip(), page)
    return CACHE_PREFIX + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_get(key):
    """从 Redis 读取缓存，返回解析后的对象或 None。"""
    r = _get_redis()
    if r is None:
        return None
    try:
        data = r.get(key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


def _cache_set(key, value, ttl=CACHE_TTL):
    """写入 Redis 缓存。"""
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        pass

# 统一 UA：不在后端引入爬虫项目的 ai 包（保持两个服务依赖隔离）
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _UA})

RISK_HINT = {412: "B站 风控拦截", 418: "豆瓣风控拦截"}


# ---------------------------------------------------------------- 掘金文章
def search_juejin(query, page=1):
    """掘金技术文章搜索（来源 juejin_article）。"""
    resp = _SESSION.get(
        "https://api.juejin.cn/search_api/v1/search",
        params={"query": query, "id_type": 0,
                "cursor": (page - 1) * 20, "limit": 20}, timeout=10).json()
    data = resp.get("data") or []
    if isinstance(data, dict):
        data = data.get("result") or []

    out = []
    for rank, item in enumerate(data, 1):
        rm = item.get("result_model") or {}
        info = rm.get("article_info") or {}
        author = (rm.get("author_user_info") or {}).get("user_name", "")
        out.append({
            "url_object_id": "juejin_{}".format(info.get("article_id")),
            "title": info.get("title") or "",
            "content": (info.get("brief_content") or "")[:300],
            "author": author,
            "tags": ["技术文章"],
            "url": "https://juejin.cn/post/{}".format(info.get("article_id")),
            "front_image_url": info.get("cover_image") or "",
            "source": "juejin_article",
            "create_date": None,
        })
        if not out[-1]["title"]:
            out.pop()
    return out


LIVE_SOURCES = {
    "juejin_article": search_juejin,
}

# 模块级一次性导入 crawler.pipelines.index_item（爬虫已合并入本项目，无需 sys.path hack）。
# 导入失败（如依赖缺失）时记日志，_cache_to_es 会优雅降级。
_index_item_fn = None
try:
    from crawler.pipelines import index_item as _index_item_fn
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning("导入 crawler.pipelines.index_item 失败（%s），"
                                          "实时搜索结果将不写入 ES", _e)


def search_live(source, query, page=1, use_cache=True):
    """调用平台搜索，返回标准化 item 列表；并起后台线程写入 ES 累积语料。

    :param use_cache: 是否使用 Redis 缓存（默认 True，缓存 5 分钟）
    """
    fn = LIVE_SOURCES.get(source)
    if fn is None:
        raise ValueError("不支持的实时来源: {}".format(source))

    # 缓存命中检查
    cache_key = _cache_key(source, query, page)
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    items = fn(query, page)
    if items:
        # 写入缓存
        if use_cache:
            _cache_set(cache_key, items)
        threading.Thread(target=_cache_to_es, args=(list(items),),
                         daemon=True).start()
    return items


def _cache_to_es(items):
    """后台线程：将实时搜索结果写入 ES 累积语料。导入失败时静默跳过。"""
    if _index_item_fn is None:
        return
    import logging
    try:
        saved = sum(1 for it in items if _index_item_fn(it, source=it.get("source")))
        logging.getLogger(__name__).info("实时结果入 ES: %d/%d", saved, len(items))
    except Exception as exc:
        logging.getLogger(__name__).warning("实时结果入 ES 失败: %s", exc)


# ---------------------------------------------------------------- 多源聚合搜索

def _relevance_score(item, query):
    """简单的相关度打分：标题命中权重高，内容命中权重低。

    用于多源聚合后的统一排序。更精准的排序可换 cross-encoder 或向量相似度。
    """
    title = (item.get("title") or "").lower()
    content = (item.get("content") or "").lower()
    q = query.lower()
    score = 0.0
    # 完整查询词命中
    if q in title:
        score += 10.0
    if q in content:
        score += 3.0
    # 分词命中（按空格/标点简单切分）
    import re
    tokens = [t for t in re.split(r"[\s,，。.!！?？、;:：]+", q) if len(t) >= 2]
    for tok in tokens:
        if tok in title:
            score += 2.0
        if tok in content:
            score += 0.5
    # 热度加权（有播放量的来源）
    view_nums = item.get("view_nums") or item.get("play_count") or 0
    if isinstance(view_nums, (int, float)) and view_nums > 0:
        import math
        score += min(math.log10(view_nums + 1) * 0.5, 2.0)
    return score


def search_all_sources(query, page=1, per_source=8, timeout=8):
    """多源聚合搜索：并发查询所有实时源，合并后按相关度统一排序。

    :param query: 搜索关键词
    :param page: 页码（每个实时源各取自己第 page 页的结果后合并）
    :param per_source: 每个源最多取多少条
    :param timeout: 单个源的超时时间（秒）
    :return: {"results": [...], "by_source": {...}, "total": total, "page_nums": n}
    """
    results_by_source = {}
    errors = {}

    def _search_one(source_key, fn):
        try:
            items = fn(query, page)[:per_source]
            results_by_source[source_key] = items
        except Exception as e:
            errors[source_key] = str(e)[:100]

    threads = []
    for source_key, fn in LIVE_SOURCES.items():
        t = threading.Thread(target=_search_one, args=(source_key, fn), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=timeout)

    # 合并所有结果，标注来源
    all_items = []
    for source_key, items in results_by_source.items():
        for it in items:
            item = dict(it)
            item.setdefault("source", source_key)
            item["_live_source"] = source_key
            all_items.append(item)

    # 按相关度排序
    all_items.sort(key=lambda it: -_relevance_score(it, query))

    # 后台写入 ES（去重后）
    if all_items:
        threading.Thread(target=_cache_to_es, args=(list(all_items),), daemon=True).start()

    # 分页：实时源没有全局 total，能取满一页就假定还有下一页
    page_size = 20
    start = (page - 1) * page_size
    paged = all_items[start:start + page_size]
    page_nums = page + 1 if len(all_items) > start + page_size else page

    return {
        "results": paged,
        "total": (page - 1) * page_size + len(all_items),
        "page": page,
        "page_nums": page_nums,
        "by_source": {k: len(v) for k, v in results_by_source.items()},
        "errors": errors,
        "live": True,
    }
