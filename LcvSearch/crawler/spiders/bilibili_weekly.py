# -*- coding: utf-8 -*-
"""
B站「每周必看」系列爬虫（由 crawler/bilibili/bilibili_weekly_must_watch_crawler.py 集成）。

老实现走搜索接口（/x/web-interface/search/type），现需要 WBI 签名已不可直连；
本次改用官方「入站必刷/每周必看」系列接口，无需签名：
    /x/web-interface/popular/series/list   期次列表（实测 388 期）
    /x/web-interface/popular/series/one    单期视频列表（约 49 个/期）

条目结构与 bilibili_hot 一致（title/desc/UP主/互动数据），额外在 tags 里
标注"每周必看第N期"，写入 ES "quotes" 索引，可搜索、可 RAG。

试跑：
    scrapy crawl bilibili_weekly                     # 只抓最新一期
    scrapy crawl bilibili_weekly -a episodes=3       # 最近 3 期
    scrapy crawl bilibili_weekly -O weekly.csv       # CSV 导出
"""
import json
from datetime import datetime

import scrapy


class BilibiliWeeklySpider(scrapy.Spider):
    name = "bilibili_weekly"
    api_base = "https://api.bilibili.com"

    custom_settings = {
        "DOWNLOAD_DELAY": 2,   # 每期一个请求，保持礼貌间隔
        "COOKIES_ENABLED": True,
        "HANDLE_HTTPSTATUS_LIST": [412, 403],
    }

    def __init__(self, episodes=1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.episodes = int(episodes)

    async def start(self):
        yield scrapy.Request("https://www.bilibili.com/",
                             callback=self.request_series_list, priority=10)

    def request_series_list(self, response):
        yield scrapy.Request(
            "{}/x/web-interface/popular/series/list".format(self.api_base),
            headers={"Referer": "https://www.bilibili.com/"},
            callback=self.parse_series_list,
        )

    def parse_series_list(self, response):
        if response.status in (412, 403):
            self.logger.warning("B站风控拦截 (status=%s)", response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("期次列表响应不是合法 JSON (status=%s)", response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        if data.get("code") != 0:
            self.logger.warning("期次列表返回 code=%s message=%s", data.get("code"), data.get("message"))
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return

        series = (data.get("data") or {}).get("list") or []
        numbers = [s.get("number") for s in series if s.get("number")][:self.episodes]
        self.logger.info("共 %d 期，抓取最近 %d 期: %s", len(series), len(numbers), numbers)

        for number in numbers:
            yield scrapy.Request(
                "{}/x/web-interface/popular/series/one?number={}".format(self.api_base, number),
                headers={"Referer": "https://www.bilibili.com/"},
                callback=self.parse_series_one,
                cb_kwargs={"number": number},
            )

    def parse_series_one(self, response, number):
        if response.status in (412, 403):
            self.logger.warning("第 %s 期风控拦截 (status=%s)", number, response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("第 %s 期响应不是合法 JSON (status=%s)", number, response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        if data.get("code") != 0:
            self.logger.warning("第 %s 期返回 code=%s", number, data.get("code"))
            return

        videos = (data.get("data") or {}).get("list") or []
        self.logger.info("第 %s 期: %d 个视频", number, len(videos))

        for rank, v in enumerate(videos, 1):
            stat = v.get("stat") or {}
            owner = v.get("owner") or {}
            pubdate = v.get("pubdate")
            bvid = v.get("bvid") or str(v.get("aid", ""))

            yield {
                "url_object_id": bvid,
                "title": v.get("title") or "未知标题",
                "content": v.get("desc") or v.get("title") or "",
                "author": owner.get("name") or "",
                "tags": [t for t in [v.get("tname"), "每周必看第{}期".format(number)] if t],
                "url": "https://www.bilibili.com/video/{}".format(bvid),
                "front_image_url": v.get("pic") or "",
                "praise_nums": stat.get("like", 0),
                "view_nums": stat.get("view", 0),
                "reply_nums": stat.get("reply", 0),
                "danmaku_nums": stat.get("danmaku", 0),
                "coin_nums": stat.get("coin", 0),
                "favorite_nums": stat.get("favorite", 0),
                "share_nums": stat.get("share", 0),
                "duration": v.get("duration", 0),
                "rank": rank,
                "source": "bilibili_weekly",
                "create_date": datetime.fromtimestamp(pubdate) if pubdate else None,
            }
