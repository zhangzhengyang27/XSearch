# -*- coding: utf-8 -*-
"""
新闻 RSS 聚合爬虫：抓官方 RSS 源写入 ES "quotes" 索引（可搜索、进词云）。

RSS 是网站官方分发格式，抓取合规且无需对抗反爬。内置 4 个源（2026-09-09 实测可用）：
    news_people     人民网综合新闻   http://www.people.com.cn/rss/politics.xml  （feed 含摘要，约100条）
    news_chinanews  中国新闻网滚动   https://www.chinanews.com/rss/scroll-news.xml
                    （feed 无摘要，自动跟进文章页抓 div.left_zw 正文）
    news_ithome     IT之家科技新闻   https://www.ithome.com/rss/  （description 为正文开头）
    news_solidot    Solidot 科技    https://www.solidot.org/index.rss

试跑：
    scrapy crawl news_rss                                  # 全部 4 个源
    scrapy crawl news_rss -a sources=news_people,news_solidot   # 指定源

去重：url 的 md5 作为 ES _id，重复抓取自动覆盖更新，可放心配定时任务每天跑。
"""
import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape

import scrapy

# RSS 源配置：key 即写入 ES 的 source 字段
FEEDS = {
    "news_people": {
        "label": "人民网",
        "url": "http://www.people.com.cn/rss/politics.xml",
        "follow_links": False,   # feed 自带摘要，无需跟进正文页
    },
    "news_chinanews": {
        "label": "中国新闻网",
        "url": "https://www.chinanews.com/rss/scroll-news.xml",
        "follow_links": True,    # feed 无摘要，跟进文章页抓正文
    },
    "news_ithome": {
        "label": "IT之家",
        "url": "https://www.ithome.com/rss/",
        "follow_links": False,
    },
    "news_solidot": {
        "label": "Solidot",
        "url": "https://www.solidot.org/index.rss",
        "follow_links": False,
    },
}

# 中新网真实文章链接模式（区分 feed 里的滚动列表页 news1.html）
CNS_ARTICLE_RE = re.compile(r"chinanews\.com\.cn/\w+/\d{4}/[\d-]+/\d+\.shtml")

# 单条正文入库上限（字符），防超长文章撑大文档
MAX_CONTENT_CHARS = 5000


def strip_html(text):
    """去掉 HTML 标签和实体，得到纯文本。"""
    if not text:
        return ""
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def parse_date(text):
    """兼容两种 feed 日期格式：RFC822（IT之家/Solidot）与 YYYY-MM-DD（人民网）。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


class NewsRssSpider(scrapy.Spider):
    name = "news_rss"

    custom_settings = {
        # RSS 是官方轻量接口，1 秒间隔足够礼貌
        "DOWNLOAD_DELAY": 1,
    }

    def __init__(self, sources="", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a sources=news_people,news_ithome 指定源；默认全部
        keys = [k.strip() for k in sources.split(",") if k.strip()] if sources else list(FEEDS)
        unknown = [k for k in keys if k not in FEEDS]
        if unknown:
            raise ValueError("未知新闻源: {}（可用: {}）".format(unknown, ", ".join(FEEDS)))
        self.feed_keys = keys

    async def start(self):
        for key in self.feed_keys:
            yield scrapy.Request(
                FEEDS[key]["url"],
                callback=self.parse_feed,
                cb_kwargs={"source_key": key},
                headers={"Accept": "application/rss+xml, application/xml, text/xml, */*"},
            )

    def parse_feed(self, response, source_key):
        feed = FEEDS[source_key]
        items = response.xpath("//channel/item")
        self.logger.info("[%s] feed 解析出 %d 条", feed["label"], len(items))
        for item in items:
            title = (item.xpath("./title/text()").get() or "").strip()
            link = (item.xpath("./link/text()").get() or "").strip()
            description = item.xpath("./description/text()").get() or ""
            pub_date = item.xpath("./pubDate/text()").get()
            if not title or not link:
                continue

            if feed["follow_links"] and CNS_ARTICLE_RE.search(link):
                yield scrapy.Request(
                    link,
                    callback=self.parse_chinanews_article,
                    cb_kwargs={"source_key": source_key, "rss_title": title,
                               "pub_date": pub_date},
                )
            else:
                content = strip_html(description) or title  # 无摘要退化为标题，保证可入库
                yield self._news_item(source_key, title, link, content, pub_date)

    def parse_chinanews_article(self, response, source_key, rss_title, pub_date):
        title = (response.xpath("//h1/text()").get() or rss_title).strip()
        # 正文容器 div.left_zw；取不到时退化为全页段落拼接
        paragraphs = response.xpath('//div[contains(@class,"left_zw")]//p//text()').getall()
        if not paragraphs:
            paragraphs = response.xpath("//p//text()").getall()
        content = "\n".join(t.strip() for t in paragraphs if len(t.strip()) > 10)
        if not content:
            self.logger.warning("正文抓取失败，退化为标题: %s", response.url)
            content = rss_title
        yield self._news_item(source_key, title, response.url, content, pub_date)

    @staticmethod
    def _news_item(source_key, title, url, content, pub_date):
        feed = FEEDS[source_key]
        return {
            "url_object_id": hashlib.md5(url.encode("utf-8")).hexdigest(),
            "title": title,
            "content": content[:MAX_CONTENT_CHARS],
            "author": feed["label"],
            "tags": ["新闻", feed["label"]],
            "url": url,
            "front_image_url": "",
            "source": source_key,
            "create_date": parse_date(pub_date),
        }
