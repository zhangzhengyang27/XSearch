# -*- coding: utf-8 -*-
"""
B站视频评论爬虫（由 crawler/bilibili/bilibili_comment_crawler.py 集成升级）。

老实现的问题：/x/v2/reply 老分页接口已降级，未登录只返回约 3 条预览评论；
本版本改用新版评论区接口 /x/v2/reply/main + cursor 游标翻页（实测可用）。

登录态（可选）：
    export BILI_COOKIE="SESSDATA=...; buvid3=...; ..."   # 完整登录 cookie
    - 配置后：完整评论列表（实测 19-20 条/页，可游标翻页）
    - 未配置：游客模式，仅返回约 3 条热评预览（B站 对未登录降级）
    cookie 只从环境变量读取，绝不写进代码/仓库。

试跑：
    scrapy crawl bilibili_comments -a bvid=BVxxxx [-a pages=5] [-O comments.csv]

数据写入 ES "quotes" 索引：title=【评论】视频标题、content=评论内容（含 IP 属地）、
author=用户、praise_nums=点赞，可被 LcvSearch 搜索、可进 RAG 语料。
未集成（预留）：二级回复、WBI 签名（接口返回 -352/-403 风控时再补）。
"""
import json
import os
from datetime import datetime

import scrapy
from scrapy.exceptions import CloseSpider


class BilibiliCommentsSpider(scrapy.Spider):
    name = "bilibili_comments"
    api_base = "https://api.bilibili.com"

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "HANDLE_HTTPSTATUS_LIST": [412, 403],
        # cookie 全部手动管理：登录态用环境变量整串下发，游客态靠首页 Set-Cookie
        "COOKIES_ENABLED": False,
    }

    def __init__(self, bvid=None, pages=5, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bvid = bvid
        self.max_pages = int(pages)
        self.cookie = os.getenv("BILI_COOKIE", "").strip()
        if self.cookie:
            self.logger.info("检测到 BILI_COOKIE，以登录态抓取完整评论")
        else:
            self.logger.info("未配置 BILI_COOKIE，游客模式（B站 仅返回热评预览）")

    def _api_headers(self):
        headers = {
            "Referer": "https://www.bilibili.com/video/{}/".format(self.bvid),
            "Accept": "application/json, text/plain, */*",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def start(self):
        if not self.bvid:
            self.logger.error("用法: scrapy crawl bilibili_comments -a bvid=BVxxxx [-a pages=5]")
            raise CloseSpider("缺少 bvid 参数")

        if self.cookie:
            yield self._view_request()
        else:
            # 游客态：先访问首页拿 buvid 等 cookie（请求间手工携带）
            yield scrapy.Request("https://www.bilibili.com/",
                                 callback=self._after_homepage,
                                 priority=10)

    def _after_homepage(self, response):
        # 把首页下发的 cookie 并入手工 cookie 串
        set_cookies = response.headers.getlist("Set-Cookie")
        if set_cookies and not self.cookie:
            joined = "; ".join(c.decode("utf-8", "ignore").split(";")[0] for c in set_cookies)
            self.cookie = joined
            self.logger.info("已从首页获取游客 cookie（%d 项）", len(set_cookies))
        yield self._view_request()

    def _view_request(self):
        return scrapy.Request(
            "{}/x/web-interface/view?bvid={}".format(self.api_base, self.bvid),
            headers=self._api_headers(),
            callback=self.parse_video,
        )

    def parse_video(self, response):
        if response.status in (412, 403):
            self.logger.warning("视频详情风控拦截 (status=%s)", response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            raise CloseSpider("风控拦截，视频信息获取失败")
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("视频详情响应不是合法 JSON (status=%s)", response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            raise CloseSpider("响应解析失败")
        if data.get("code") != 0:
            self.logger.warning("视频详情返回 code=%s message=%s",
                                data.get("code"), data.get("message"))
            self.crawler.stats.inc_value("bilibili/api_rejected")
            raise CloseSpider("视频信息获取失败")

        video = data.get("data") or {}
        self.aid = video.get("aid")
        self.video_title = video.get("title") or ""
        self.logger.info("视频: %s (aid=%s)，开始抓取评论（最多 %d 页）",
                         self.video_title, self.aid, self.max_pages)
        yield self._replies_request(next_cursor=None, page=1)

    def _replies_request(self, next_cursor, page):
        url = "{}/x/v2/reply/main?type=1&oid={}&mode=3&ps=20".format(self.api_base, self.aid)
        if next_cursor is not None:
            url += "&next={}".format(next_cursor)
        return scrapy.Request(
            url,
            headers=self._api_headers(),
            callback=self.parse_replies,
            cb_kwargs={"page": page},
        )

    def parse_replies(self, response, page):
        if response.status in (412, 403):
            self.logger.warning("评论接口风控拦截 (status=%s, page=%d)", response.status, page)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("评论接口响应不是合法 JSON (status=%s, page=%d)",
                                response.status, page)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        if data.get("code") != 0:
            self.logger.warning("评论接口返回 code=%s message=%s",
                                data.get("code"), data.get("message"))
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return

        body = data.get("data") or {}
        cursor = body.get("cursor") or {}
        replies = body.get("replies") or []
        self.logger.info("第 %d 页评论: %d 条", page, len(replies))

        for r in replies:
            member = r.get("member") or {}
            content = r.get("content") or {}
            message = content.get("message") or ""
            location = (r.get("reply_control") or {}).get("location", "")
            ctime = r.get("ctime")

            yield {
                # rpid 是评论天然唯一 ID
                "url_object_id": "{}_{}".format(self.bvid, r.get("rpid")),
                "title": "【评论】{}".format(self.video_title),
                "content": "{}（IP属地：{}）".format(message, location.replace("IP属地：", ""))
                          if location else message,
                "author": member.get("uname") or "",
                "tags": ["B站评论"],
                "url": "https://www.bilibili.com/video/{}".format(self.bvid),
                "praise_nums": r.get("like", 0),
                "source": "bilibili_comments",
                "create_date": datetime.fromtimestamp(ctime) if ctime else None,
            }

        next_cursor = cursor.get("next")
        has_more = not cursor.get("is_end") and replies
        if has_more and next_cursor is not None and page < self.max_pages:
            yield self._replies_request(next_cursor=next_cursor, page=page + 1)
