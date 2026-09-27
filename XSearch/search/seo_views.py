# -*- coding: utf-8 -*-
"""面向搜索引擎的两个纯文本出口：/robots.txt 与 /sitemap.xml。

单独成模块而不是塞进 api_views：那个文件已经一千行，再加只会更难读。

为什么 sitemap 里是 /ai/detail?id=…：本站是 SPA，内容页没有独立的服务端 URL，
而 Googlebot 会执行 JS，所以带 id 的详情页是可被收录的最小单元。
新闻条目没有详情页（正文在源站），因此不进 sitemap——列了也收录不到东西。
"""
import logging

from django.http import HttpResponse
from django.views.decorators.http import require_http_methods

from search.api_views import AI_SOURCES, INDEX, client, redis_cli

logger = logging.getLogger(__name__)

SITEMAP_LIMIT = 5000        # 单文件上限，避免一次拉爆内存
SITEMAP_CACHE_TTL = 3600    # 秒；sitemap 不需要实时

# 站点栏目页：常驻、优先抓
STATIC_PATHS = [
    ("/ai", "daily", "0.8"),
    ("/search", "weekly", "0.7"),
    ("/news", "daily", "0.6"),
    ("/rankings", "daily", "0.5"),
]

# 管理页与登录页：不该出现在搜索结果里
DISALLOWED = ["/crawl", "/dbadmin", "/login", "/admin/"]


@require_http_methods(["GET"])
def robots_txt(request):
    base = request.build_absolute_uri("/").rstrip("/")
    lines = ["User-agent: *", "Allow: /"]
    lines += ["Disallow: {}".format(p) for p in DISALLOWED]
    lines += ["", "Sitemap: {}/sitemap.xml".format(base), ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def _detail_urls(base):
    """近期 AI 内容详情页；ES 不可用时返回空列表（sitemap 降级为只有栏目页）。"""
    try:
        resp = client.search(
            index=INDEX,
            query={"terms": {"source": list(AI_SOURCES)}},
            sort=[{"create_date": {"order": "desc", "missing": "_last"}}],
            _source=["create_date"],
            size=SITEMAP_LIMIT,
        )
    except Exception as e:
        logger.warning("sitemap 取文档失败，降级为只输出栏目页：%s", e)
        return []
    out = []
    for hit in resp["hits"]["hits"]:
        doc_id = hit.get("_id")
        if not doc_id:
            continue
        date = str((hit.get("_source") or {}).get("create_date") or "")[:10]
        url = "{}/ai/detail?id={}".format(base, doc_id)
        out.append((url, date))
    return out


def _render(urls):
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, changefreq, priority in urls["static"]:
        parts.append("<url><loc>{}</loc><changefreq>{}</changefreq>"
                     "<priority>{}</priority></url>".format(loc, changefreq, priority))
    for loc, lastmod in urls["detail"]:
        parts.append("<url><loc>{}</loc>{}</url>".format(
            loc, "<lastmod>{}</lastmod>".format(lastmod) if lastmod else ""))
    parts.append("</urlset>")
    return "".join(parts)


@require_http_methods(["GET"])
def sitemap_xml(request):
    base = request.build_absolute_uri("/").rstrip("/")
    cache_key = "sitemap:{}".format(base)
    try:
        cached = redis_cli.get(cache_key)
        if cached:
            return HttpResponse(cached, content_type="application/xml; charset=utf-8")
    except Exception:
        pass  # Redis 挂了不影响输出

    urls = {"static": [("{}{}".format(base, path), freq, prio)
                       for path, freq, prio in STATIC_PATHS],
            "detail": _detail_urls(base)}
    xml = _render(urls)
    try:
        redis_cli.setex(cache_key, SITEMAP_CACHE_TTL, xml)
    except Exception:
        pass
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")
