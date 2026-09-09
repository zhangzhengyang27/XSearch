# -*- coding: utf-8 -*-
"""
AIHOT AI 资讯爬虫：把 aihot.news 的 AI 精选动态与每日日报写入 ES。

数据来源（站方官方公开 API，llms.txt 声明匿名只读、个人非商业免费，限速 60 req/min）：
    /api/v1/items?mode=selected&window=7d   LLM 摘要+打分的精选动态（-a full=1 拉全量动态）
    /api/v1/dailies + /api/v1/dailies/{date}  精编日报（默认最近 3 期，-a daily_days=N 可调）

字段映射：
    title=标题  content=LLM摘要(+入选理由)  author=原始信源名  rating=AI评分(0-100)
    tags=[AI资讯/AI日报, 分类]  url=AIHOT 站内阅读页（按站方要求保留 attribution）

幂等性：url_object_id 用 AIHOT 稳定 id（aihot_{id} / aihot_daily_{date}），
重复抓取自动覆盖，可放心配定时任务。

试跑：
    scrapy crawl aihot_news                # 精选动态（7天窗口）+ 当日日报
    scrapy crawl aihot_news -a full=1      # 全量动态（含未精选，噪音更多）
"""
import json
from datetime import datetime

import scrapy

API_BASE = "https://aihot.news"
UA = "LcvSearch-crawler/1.0 (personal learning project)"

# 单页条数与最大翻页数（约 300 条/次，远够 7 天窗口）
PAGE_LIMIT = 50
MAX_PAGES = 6
MAX_CONTENT_CHARS = 5000


class AihotNewsSpider(scrapy.Spider):
    name = "aihot_news"

    custom_settings = {
        "DOWNLOAD_DELAY": 2,   # 站方限额 60 req/min，2 秒间隔足够礼貌
        "HANDLE_HTTPSTATUS_LIST": [429, 503],
    }

    def __init__(self, full=0, pages=MAX_PAGES, daily_days=3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = "all" if bool(int(full)) else "selected"
        self.max_pages = max(int(pages), 1)
        # 日报抓取期数：今天 8 点的日报发布前，latest 仍是昨天的，
        # 抓最近 N 期保证任何时点打开都有数据（幂等覆盖）
        self.daily_days = max(int(daily_days), 1)

    async def start(self):
        headers = {"User-Agent": UA, "Accept": "application/json"}
        yield scrapy.Request(
            "{}/api/v1/items?mode={}&window=7d&limit={}".format(
                API_BASE, self.mode, PAGE_LIMIT),
            headers=headers,
            callback=self.parse_items,
            meta={"page": 1},
        )
        yield scrapy.Request(
            "{}/api/v1/dailies".format(API_BASE),
            headers=headers,
            callback=self.parse_dailies_list,
        )

    # ---------------------------------------------------------------- 精选动态
    def parse_items(self, response):
        if response.status in (429, 503):
            self.logger.warning("AIHOT 限流/不可用 (status=%s)，items 部分放弃", response.status)
            self.crawler.stats.inc_value("aihot/ratelimited")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("items 响应不是合法 JSON")
            return

        items = data.get("items") or []
        page = response.meta["page"]
        self.logger.info("AIHOT %s 动态第 %d 页: %d 条", self.mode, page, len(items))
        for it in items:
            parsed = self._parse_item(it)
            if parsed:
                yield parsed

        # cursor 翻页
        next_cursor = (data.get("page") or {}).get("nextCursor")
        if next_cursor and page < self.max_pages and (data.get("page") or {}).get("hasMore"):
            yield scrapy.Request(
                "{}/api/v1/items?mode={}&window=7d&limit={}&cursor={}".format(
                    API_BASE, self.mode, PAGE_LIMIT, next_cursor),
                headers={"User-Agent": UA, "Accept": "application/json"},
                callback=self.parse_items,
                meta={"page": page + 1},
                dont_filter=True,
            )

    def _parse_item(self, it):
        title = (it.get("title") or "").strip()
        summary = (it.get("summary") or "").strip()
        if not title or not summary:
            return None
        links = it.get("links") or {}
        content_parts = [summary]
        if it.get("reason"):
            content_parts.append("入选理由：{}".format(it["reason"].strip()))
        src = it.get("source") or {}
        tags = ["AI资讯"]
        if it.get("category"):
            tags.append(it["category"])
        if it.get("selected"):
            tags.append("AI精选")
        return {
            "url_object_id": "aihot_{}".format(it.get("id", "")),
            "title": title,
            "content": "\n".join(content_parts)[:MAX_CONTENT_CHARS],
            "author": (src.get("name") or "AIHOT").strip(),
            "tags": tags,
            "url": links.get("aihot") or "",
            "front_image_url": "",
            "rating": it.get("score"),          # AI 评分 0-100，搜索卡显示为星级
            "source": "aihot_news",
            "create_date": self._parse_iso(it.get("publishedAt")),
        }

    # ---------------------------------------------------------------- 每日日报
    def parse_dailies_list(self, response):
        """日报索引：取最近 N 期，逐期抓取完整报告。"""
        if response.status in (429, 503):
            self.logger.warning("AIHOT 限流/不可用 (status=%s)，日报放弃", response.status)
            self.crawler.stats.inc_value("aihot/ratelimited")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("dailies 响应不是合法 JSON")
            return
        dates = [(it.get("date") or "").strip()
                 for it in (data.get("items") or [])][:self.daily_days]
        dates = [d for d in dates if d]
        self.logger.info("AIHOT 日报索引，抓取最近 %d 期: %s", len(dates), dates)
        for date in dates:
            yield scrapy.Request(
                "{}/api/v1/dailies/{}".format(API_BASE, date),
                headers={"User-Agent": UA, "Accept": "application/json"},
                callback=self.parse_daily,
            )

    def parse_daily(self, response):
        if response.status in (429, 503):
            self.logger.warning("AIHOT 限流 (status=%s)，日报放弃", response.status)
            return
        try:
            report = (json.loads(response.text) or {}).get("report") or {}
        except json.JSONDecodeError:
            self.logger.warning("dailies 响应不是合法 JSON")
            return
        date = report.get("date") or ""
        if not date:
            return

        lines = []
        lead = report.get("lead") or {}
        lead_title = (lead.get("title") or "").strip()
        if lead_title:
            lines.append("头条：{}".format(lead_title))
        lead_para = (lead.get("paragraph") or "").strip()
        if lead_para:
            lines.append(lead_para)
        for section in report.get("sections") or []:
            label = (section.get("label") or "").strip()
            if label:
                lines.append("")
                lines.append("【{}】".format(label))
            for it in section.get("items") or []:
                t = (it.get("title") or "").strip()
                s = (it.get("summary") or "").strip()
                if t:
                    lines.append("· {}".format(t))
                if s and s != t:
                    lines.append("  {}".format(s[:200]))
        for flash in report.get("flashes") or []:
            flash_text = (flash.get("title") or flash if isinstance(flash, str)
                          else flash.get("summary") or "").strip() if flash else ""
            if flash_text:
                lines.append("快讯：{}".format(flash_text[:150]))

        content = "\n".join(lines).strip()[:MAX_CONTENT_CHARS]
        if not content:
            return
        self.logger.info("AIHOT 日报 %s：%d 字", date, len(content))
        yield {
            "url_object_id": "aihot_daily_{}".format(date),
            "title": "AIHOT AI 日报（{}）".format(date),
            "content": content,
            "author": "AIHOT",
            "tags": ["AI日报", "AI资讯"],
            "url": ((report.get("links") or {}).get("aihot")) or "{}/daily".format(API_BASE),
            "front_image_url": "",
            "source": "aihot_daily",
            "create_date": self._parse_iso(report.get("generatedAt")),
        }

    @staticmethod
    def _parse_iso(text):
        if not text:
            return None
        try:
            return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        except ValueError:
            return None
