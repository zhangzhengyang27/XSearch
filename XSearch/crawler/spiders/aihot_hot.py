# -*- coding: utf-8 -*-
"""
AI 热点榜爬虫：抓 AIHOT（aihot.news）官方公开 API 的热点榜写入 ES。

数据来源：/api/v1/hot-topics（48 小时内多信源共同印证的 AI 事件 Top 10），
         /api/v1/stories/{publicId}（事件报道时间线 + AI 综述 digest）。
站方 llms.txt 明确允许匿名只读访问（个人非商业免费），限速 60 次/分，
本爬虫单次约 20 个请求、2 秒间隔，远低于限额。

字段映射：title=事件标题、content=AI 综述 digest、view_nums=信源印证数、
         rank=榜单排名、url=AIHOT 站内引用页（按站方要求保留 attribution）。

榜单语义：热点榜是"当前状态"——爬虫结束时删除已跌出本轮 Top 10 的旧文档，
榜单不会越攒越长（_id 用事件 publicId，稳定且幂等）。

试跑：
    scrapy crawl aihot_hot
"""
import json
from datetime import datetime

import scrapy
from scrapy import signals


API_BASE = "https://aihot.news"
UA = "XSearch-crawler/1.0 (personal learning project)"


class AihotHotSpider(scrapy.Spider):
    name = "aihot_hot"

    custom_settings = {
        # 站方限额 60 req/min/IP，20 个请求 2 秒间隔足够礼貌
        "DOWNLOAD_DELAY": 2,
        "HANDLE_HTTPSTATUS_LIST": [429, 503],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_ids = set()  # 本轮实际入库的事件 id，收尾时清理跌出榜单的旧文档

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        # 基类 from_crawler 负责挂 crawler/settings（_set_crawler）并连接 close 信号，
        # 不要在这里手动赋值绕过它（会导致 spider.settings 缺失而崩溃）
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._prune_stale, signal=signals.spider_closed)
        return spider

    async def start(self):
        yield scrapy.Request(
            "{}/api/v1/hot-topics".format(API_BASE),
            headers={"User-Agent": UA, "Accept": "application/json"},
            callback=self.parse_hot,
        )

    def parse_hot(self, response):
        if response.status in (429, 503):
            self.logger.warning("AIHOT 限流/不可用 (status=%s)，本次放弃", response.status)
            self.crawler.stats.inc_value("aihot/ratelimited")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("hot-topics 响应不是合法 JSON")
            return
        topics = data if isinstance(data, list) else (data.get("items") or [])
        self.logger.info("AIHOT 热点榜 %d 条", len(topics))

        for topic in topics:
            links = topic.get("links") or {}
            story_url = links.get("story") or ""
            # publicId 只取自 links.story（站方要求不要猜测）
            public_id = story_url.rsplit("/", 1)[-1] if story_url else ""
            item_id = "aihot_{}".format(public_id or topic.get("id", ""))
            self._seen_ids.add(item_id)

            base = {
                "url_object_id": item_id,
                "title": (topic.get("title") or "").strip(),
                "content": (topic.get("title") or "").strip(),
                "author": "AIHOT",
                "tags": ["AI热点", "AIHOT"],
                "url": links.get("aihot") or "",
                "front_image_url": "",
                "view_nums": topic.get("sourceCount"),   # 信源印证数作为热度展示
                "rank": topic.get("rank"),
                "source": "aihot_hot",
                "create_date": self._parse_iso(topic.get("latestAt")),
            }
            if not base["title"]:
                continue
            # 先入一条基础数据保底；story 抓到 AI 综述后用同一 _id 覆盖为完整版
            yield dict(base)
            if public_id:
                yield scrapy.Request(
                    "{}/api/v1/stories/{}".format(API_BASE, public_id),
                    headers={"User-Agent": UA, "Accept": "application/json"},
                    callback=self.parse_story,
                    cb_kwargs={"base": base},
                    dont_filter=False,
                )

    def parse_story(self, response, base):
        if response.status in (429, 503):
            self.logger.warning("story 限流 (status=%s)，保留基础数据: %s",
                                response.status, base["title"][:30])
            return
        try:
            story = (json.loads(response.text) or {}).get("story") or {}
        except json.JSONDecodeError:
            return
        digest = (story.get("digest") or "").strip()
        latest = (story.get("latest") or "").strip()
        if not digest:
            return  # 无综述时保留基础数据
        parts = [digest]
        if latest and latest not in digest:
            parts.append("最新进展：{}".format(latest))
        item = dict(base)
        item["content"] = "\n".join(parts)[:5000]
        yield item

    def _prune_stale(self):
        """榜单是"当前状态"：删除本轮未出现的旧热点，避免榜单无限累积。"""
        if not self._seen_ids:
            return
        try:
            from elasticsearch import Elasticsearch
            es = Elasticsearch(
                self.crawler.settings.getlist("ES_HOSTS") or ["http://127.0.0.1:9200"],
                request_timeout=10,
            )
            resp = es.delete_by_query(
                index=self.crawler.settings.get("ES_INDEX", "quotes"),
                query={"bool": {
                    "must": [{"term": {"source": "aihot_hot"}}],
                    "must_not": [{"terms": {"url_object_id": list(self._seen_ids)}}],
                }},
                refresh=True,
            )
            deleted = resp.get("deleted", 0)
            if deleted:
                self.logger.info("清理跌出榜单的旧热点 %d 条", deleted)
        except Exception as e:
            self.logger.warning("清理旧热点失败（不影响本次入库）: %s", e)

    @staticmethod
    def _parse_iso(text):
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
