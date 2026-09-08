# -*- coding: utf-8 -*-
"""
B站热门/排行榜视频爬虫（由独立脚本 bilibili_hot_videos.py 移植集成）。

数据源：B站公开 Web API（无需登录）
    /x/web-interface/popular           每日热门
    /x/web-interface/popular/precious  入站必刷（backup）
    /x/web-interface/ranking/v2        排行榜（rid=0 全站）

与老脚本的差异：
    - 挂进 Scrapy 框架：指纹头/住宅代理/ES 入库全部复用项目基础设施；
    - 数据写入 ES "quotes" 索引（title/desc/UP主/分区/互动数据），
      可被 LcvSearch 搜索与 RAG 问答，不再只落 CSV；
    - 仍可导出 CSV：scrapy crawl bilibili_hot -O bilibili.csv（Scrapy 自带 feed 导出）；
    - 风控预留：当前三个接口实测无需 WBI 签名即可访问；若后续 B站 收紧
      （返回 code=-352 或 -403），需按 bilibili-API-collect 的 WBI 方案加签名，
      届时在 _wbi_sign() 中补实现。

试跑：
    scrapy crawl bilibili_hot                        # 热门 20 条
    scrapy crawl bilibili_hot -a mode=ranking        # 全站排行榜
    scrapy crawl bilibili_hot -a mode=precious       # 入站必刷
"""
import json
from datetime import datetime

import scrapy


class BilibiliHotSpider(scrapy.Spider):
    name = "bilibili_hot"
    api_base = "https://api.bilibili.com"

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        # 412 = B站 风控拦截，让响应进入 parse 以便记录 code 信息
        "HANDLE_HTTPSTATUS_LIST": [412, 403],
    }

    def __init__(self, mode="hot", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # mode: hot=每日热门 | precious=入站必刷 | ranking=排行榜
        self.mode = mode

    async def start(self):
        paths = {
            "hot": "/x/web-interface/popular",
            "precious": "/x/web-interface/popular/precious",
            "ranking": "/x/web-interface/ranking/v2",
        }
        path = paths.get(self.mode, paths["hot"])
        yield scrapy.Request(
            self.api_base + path + ("?rid=0&type=all" if self.mode == "ranking" else ""),
            headers={
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
            },
            callback=self.parse_list,
        )

    def parse_list(self, response):
        # 风控拦截（412/403）时响应体为 HTML，直接 json.loads 会崩溃
        if response.status in (412, 403):
            self.logger.warning("B站风控拦截 (status=%s)，可能需要 WBI 签名或登录 cookie",
                                response.status)
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.warning("响应不是合法 JSON (status=%s, body前100字=%s)",
                                response.status, response.text[:100])
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return
        code = data.get("code")
        if code != 0:
            # -352/-403 = 风控拦截，需要 WBI 签名或 buvid cookie 时在此扩展
            self.logger.warning("API 返回 code=%s message=%s（可能触发风控）",
                                code, data.get("message"))
            self.crawler.stats.inc_value("bilibili/api_rejected")
            return

        videos = (data.get("data") or {}).get("list") or []
        self.logger.info("获取到 %d 个视频 (%s)", len(videos), self.mode)

        for rank, v in enumerate(videos, 1):
            stat = v.get("stat") or {}
            owner = v.get("owner") or {}
            pubdate = v.get("pubdate")
            bvid = v.get("bvid") or str(v.get("aid", ""))
            desc = v.get("desc") or ""

            yield {
                "url_object_id": bvid,                      # 天然唯一 ID，直接作 ES _id 去重
                "title": v.get("title") or "未知标题",
                "content": desc or v.get("title") or "",
                "author": owner.get("name") or "",
                "tags": [v["tname"]] if v.get("tname") else [],
                "url": "https://www.bilibili.com/video/{}".format(bvid),
                "front_image_url": v.get("pic") or "",
                "praise_nums": stat.get("like", 0),         # 点赞
                "view_nums": stat.get("view", 0),           # 播放
                "reply_nums": stat.get("reply", 0),         # 评论
                "danmaku_nums": stat.get("danmaku", 0),     # 弹幕
                "coin_nums": stat.get("coin", 0),           # 投币
                "favorite_nums": stat.get("favorite", 0),   # 收藏
                "share_nums": stat.get("share", 0),         # 分享
                "duration": v.get("duration", 0),           # 秒
                "rank": rank,
                "source": "bilibili_hot",
                "create_date": datetime.fromtimestamp(pubdate) if pubdate else None,
            }
