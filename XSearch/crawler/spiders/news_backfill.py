# -*- coding: utf-8 -*-
"""
新闻历史回填爬虫：抓中新网按日期的滚动新闻存档页，把过去 N 天的标题数据补入 ES。

背景：RSS feed 只有最新窗口（中新网约 30 条），无法回溯历史。
中新网提供公开的按日期滚动存档页（/scroll-news/{yyyy}/{mmdd}/news.shtml），
可逐日回溯。这是四个新闻源中唯一有公开历史存档的。

取舍：回填只存「标题 + 链接 + 日期」（content=标题），不跟进文章页抓正文——
180 天约 1 万+ 篇文章，逐篇抓取需要数小时且对站点不礼貌；后续如果某篇文章
进入当日 RSS 窗口，日常的 news_rss 爬虫会以相同 _id 覆盖并补全正文。

试跑：
    scrapy crawl news_backfill                     # 回填最近 180 天
    scrapy crawl news_backfill -a days=30          # 最近 30 天
"""
import hashlib
from datetime import datetime, timedelta

import scrapy

MAX_TITLE_CHARS = 300


class NewsBackfillSpider(scrapy.Spider):
    name = "news_backfill"

    custom_settings = {
        # 逐日一个列表页请求，1.5 秒间隔对站点足够礼貌
        "DOWNLOAD_DELAY": 1.5,
        "HANDLE_HTTPSTATUS_LIST": [404],
    }

    def __init__(self, days=180, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.days = max(int(days), 1)

    async def start(self):
        base = datetime.now()
        for offset in range(self.days):
            day = base - timedelta(days=offset)
            yield scrapy.Request(
                "https://www.chinanews.com.cn/scroll-news/{}/{:02d}{:02d}/news.shtml".format(
                    day.year, day.month, day.day),
                callback=self.parse_day,
                cb_kwargs={"date": day.strftime("%Y-%m-%d")},
                errback=self.skip_day,
            )

    async def skip_day(self, failure):
        """404（当天无存档）等失败静默跳过。"""
        pass

    def parse_day(self, response, date):
        if response.status == 404:
            return
        # 存档页文章链接为协议相对格式 //www.chinanews.com.cn/{版块}/{yyyy}/{mm-dd}/{id}.shtml，
        # 带锚文本的才是文章（频道导航无日期路径）
        seen = set()
        items = []
        for a in response.css("a"):
            href = a.attrib.get("href") or ""
            if "/20" not in href or not href.endswith(".shtml"):
                continue
            if "chinanews.com" not in href:
                continue
            title = (a.xpath("string(.)").get() or "").strip()
            if len(title) < 6 or href in seen:
                continue
            seen.add(href)
            items.append((href, title))

        if items:
            self.logger.info("存档 %s：%d 条", date, len(items))
        for href, title in items:
            if href.startswith("//"):
                href = "https:" + href
            yield {
                "url_object_id": hashlib.md5(href.encode("utf-8")).hexdigest(),
                "title": title[:MAX_TITLE_CHARS],
                "content": title[:MAX_TITLE_CHARS],
                "author": "中国新闻网",
                "tags": ["新闻", "中国新闻网", "回填"],
                "url": href,
                "front_image_url": "",
                "source": "news_chinanews",
                "create_date": date,
            }
