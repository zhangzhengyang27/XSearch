# -*- coding: utf-8 -*-
"""
豆瓣读书/电影 Top250 榜单爬虫（由 crawler/03-advanced/projects/douban_top250*.py 集成）。

老实现：requests + BeautifulSoup，结果只落 Excel。
本版本：Scrapy 化 + 指纹头中间件 + 写入 ES "quotes" 索引，可搜索、可 RAG
（"评分最高的电影""推荐几本高分小说" 这类问题直接命中榜单语料）。

字段映射：
    电影  title=片名  author=导演  content=信息行+金句  tags=[榜单, 年份]
    图书  title=书名  author=作者  content=出版信息+短评  tags=[榜单, 出版年]
    公共  rating=评分  rank=排名  url=条目页  front_image_url=封面

试跑：
    scrapy crawl douban_top250                      # 电影 Top250（250 条）
    scrapy crawl douban_top250 -a kind=book         # 图书 Top250
    scrapy crawl douban_top250 -a kind=book -O douban_books.csv
"""
import re

import scrapy


class DoubanTop250Spider(scrapy.Spider):
    name = "douban_top250"

    custom_settings = {
        "DOWNLOAD_DELAY": 2,   # 豆瓣对频率敏感，保持礼貌抓取
        "HANDLE_HTTPSTATUS_LIST": [403, 418],
    }

    def __init__(self, kind="movie", *args, **kwargs):
        super().__init__(*args, **kwargs)
        kind = kind if kind in ("movie", "book") else "movie"
        self.kind = kind
        self.base_url = "https://{}.douban.com/top250".format(
            "movie" if kind == "movie" else "book")
        self.list_tag = ["豆瓣{}Top250".format("电影" if kind == "movie" else "图书")]

    async def start(self):
        for start in range(0, 250, 25):
            yield scrapy.Request(
                "{}?start={}".format(self.base_url, start),
                headers={"Referer": "https://www.douban.com/",
                         "Accept-Language": "zh-CN,zh;q=0.9"},
                cb_kwargs={"start": start},
            )

    def parse(self, response, start):
        if response.status in (403, 418):
            self.logger.warning("豆瓣风控拦截（%s），请降低频率或配置住宅代理", response.status)
            self.crawler.stats.inc_value("douban/blocked")
            return

        rows = list(self._parse_movie(response) if self.kind == "movie"
                    else self._parse_book(response, start))
        self.logger.info("start=%s 解析出 %d 条", start, len(rows))
        for row in rows:
            row["tags"] = list(self.list_tag) + ([row.pop("year")] if row.get("year") else [])
            row["source"] = "douban_{}".format(self.kind)
            yield row

    @staticmethod
    def _year(text):
        m = re.search(r"(19|20)\d{2}", text or "")
        return m.group(0) if m else None

    # ---------- 电影 ----------
    def _parse_movie(self, response):
        for item in response.css("ol.grid_view li"):
            link = item.css("div.pic a::attr(href)").extract_first("")
            info = item.css("p::text").extract()
            info_line = " ".join(t.strip() for t in info if t.strip())
            quote = item.css("span.inq::text").extract_first("") or ""
            title_parts = item.css("span.title::text").extract()
            match = re.search(r"/subject/(\d+)/", link or "")

            # info 行形如 "导演: xx 主演: yy / 1994 / 美国 / 剧情"；
            # 无"主演"分隔时截断到年份，避免把年份/地区塞进导演字段
            author = info_line.split("主演")[0].replace("导演:", "").strip() \
                if "主演" in info_line else info_line[:40]

            yield {
                "url_object_id": match.group(1) if match else None,
                "title": (title_parts[0].split("/")[0].strip()
                          if title_parts else item.css("img::attr(alt)").extract_first("")),
                "author": author,
                "content": "。".join(x for x in (info_line, "金句：{}".format(quote)) if x.strip()),
                "rating": item.css("span.rating_num::text").extract_first(""),
                "rank": item.css("em::text").extract_first(""),
                "front_image_url": item.css("img::attr(src)").extract_first(""),
                "year": self._year(info_line),
                "url": link or "",
            }

    # ---------- 图书 ----------
    def _parse_book(self, response, start):
        for offset, item in enumerate(response.css("tr.item"), start=start + 1):
            link = item.css("div.pl2 a::attr(href)").extract_first("")
            # 标题节点含嵌套 span，直接取整个 a 的文本再清洗
            title = " ".join(t.strip() for t in item.css("div.pl2 a::text").extract() if t.strip())
            pl = item.css("p.pl::text").extract_first("") or ""
            quote = item.css("p.quote span.inq::text").extract_first("") or ""
            match = re.search(r"/subject/(\d+)/", link or "")
            parts = pl.strip().split(" / ")

            yield {
                "url_object_id": match.group(1) if match else None,
                "title": title.split("\n")[0].strip() or "未知书名",
                "author": parts[0].strip() if parts else "",
                "content": "。".join(x for x in (pl.strip(), "短评：{}".format(quote)) if x.strip()),
                "rating": item.css("span.rating_nums::text").extract_first(""),
                "rank": offset,
                "front_image_url": item.css("img::attr(src)").extract_first(""),
                "year": self._year(pl),
                "url": link or "",
            }
