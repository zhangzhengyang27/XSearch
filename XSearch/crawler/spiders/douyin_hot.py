# -*- coding: utf-8 -*-
"""
抖音热点榜爬虫。

数据源：抖音网页版热点榜 https://www.douyin.com/hot
使用 scrapy-playwright 渲染页面，从页面 DOM 中提取热点榜数据。

数据字段：排名、标题、热度值、标签（新/热/沸）、话题链接

试跑：
    scrapy crawl douyin_hot
"""
import re
from datetime import datetime

import scrapy


class DouyinHotSpider(scrapy.Spider):
    name = "douyin_hot"

    custom_settings = {
        "DOWNLOAD_DELAY": 2,
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        },
        # 抖音可能返回 412/403，让响应进入 parse 以便记录
        "HANDLE_HTTPSTATUS_LIST": [412, 403, 404],
    }

    async def start(self):
        # Scrapy 2.13+ 的起始请求入口（取代旧的 start_requests）
        yield scrapy.Request(
            "https://www.douyin.com/hot",
            meta={
                "playwright": True,
                "playwright_include_page": True,
                "playwright_context_kwargs": {
                    "user_agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36"),
                    "locale": "zh-CN",
                },
                "playwright_page_methods": [
                    # 等待 body 加载完成即返回，不等所有资源（抖音页面资源多，加载慢）
                    # 用 wait_for_timeout 在 parse 中等待数据渲染
                ],
            },
            callback=self.parse,
            errback=self.errback,
        )

    async def parse(self, response):
        page = response.meta["playwright_page"]
        try:
            if response.status in (412, 403):
                self.logger.warning("抖音风控拦截 (status=%s)，可能需要登录 cookie",
                                    response.status)
                return

            # 等待热点榜列表加载
            await page.wait_for_timeout(5000)

            # 从页面文本解析热点榜数据（比 DOM 选择器更可靠）
            # 抖音热点榜格式：标题 + "XX万热度"，前3名不显示数字排名
            page_text = await page.inner_text("body")

            items = self._parse_hot_list(page_text)

            if not items:
                self.logger.warning("未能从页面提取到热点榜数据，可能页面结构已变化或被风控")
                # 保存调试截图
                try:
                    await page.screenshot(path="/tmp/douyin_hot_debug.png", full_page=True)
                    self.logger.info("调试截图已保存到 /tmp/douyin_hot_debug.png")
                    # 保存页面文本以便调试
                    with open("/tmp/douyin_hot_debug.txt", "w", encoding="utf-8") as f:
                        f.write(page_text[:5000])
                except Exception:
                    pass
                return

            self.logger.info("获取到 %d 条抖音热点榜数据", len(items))

            for item in items:
                rank = item.get("rank", 0)
                title = item.get("title", "")
                hot_value = item.get("hot_value", 0)
                url = item.get("url", "https://www.douyin.com/hot")

                yield {
                    "url_object_id": "douyin_hot_{}".format(rank),
                    "title": title,
                    "content": "抖音热点榜第{}名：{}".format(rank, title),
                    "author": "抖音热点",
                    "tags": ["抖音热点"],
                    "url": url,
                    "front_image_url": "",
                    "view_nums": hot_value,  # 热度值作为播放量展示
                    "rank": rank,
                    "source": "douyin_hot",
                    "create_date": datetime.now(),
                }
        finally:
            await page.close()

    @staticmethod
    def _parse_hot_list(page_text):
        """从页面文本中解析抖音热点榜数据。

        抖音热点榜文本格式（前3名无数字排名，第4名起有数字）：
            抖音热榜
            标题1
            1207.4万热度
            标题2
            1191.7万热度
            ...
            4
            标题4
            1128.6万热度

        解析策略：按行扫描，遇到"XX万热度"行时，取前面最近的非空行作为标题。
        """
        items = []
        lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]

        # 找到"抖音热榜"的起始位置
        start_idx = 0
        for i, ln in enumerate(lines):
            if "抖音热榜" in ln or "热点榜" in ln:
                start_idx = i + 1
                break

        # 从起始位置扫描，找到所有"热度"行
        seen_titles = set()
        rank = 0
        i = start_idx
        while i < len(lines):
            ln = lines[i]
            # 匹配热度行：如 "1207.4万热度"、"1191.7万热度"
            hot_match = re.match(r'^([\d.]+)\s*万\s*热度$', ln)
            if hot_match:
                hot_value = int(float(hot_match.group(1)) * 10000)
                # 标题是热度行前面最近的非空、非纯数字、非"热度"的行
                title = ""
                for j in range(i - 1, max(i - 5, start_idx - 1), -1):
                    candidate = lines[j]
                    # 跳过纯数字（排名）、空行、已作为标题的行
                    if (candidate and not re.match(r'^\d+$', candidate)
                            and "热度" not in candidate
                            and candidate not in seen_titles
                            and len(candidate) > 1 and len(candidate) < 100):
                        title = candidate
                        break
                if title:
                    rank += 1
                    seen_titles.add(title)
                    items.append({
                        "rank": rank,
                        "title": title,
                        "hot_value": hot_value,
                        "url": "https://www.douyin.com/search/{}".format(
                            re.sub(r'\s+', '', title)),
                    })
            i += 1

        return items[:50]

    async def errback(self, failure):
        self.logger.error("抖音热点榜请求失败: %s", failure)
        # include_page 模式下请求失败也要关闭页面，否则浏览器页面泄漏
        page = failure.request.meta.get("playwright_page")
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
