# -*- coding: utf-8 -*-
"""
AI 时代的标准爬虫范式示例（可运行）。

演示三件取代老课程手段的事：
1. scrapy-playwright 渲染 JS 页面 —— 取代 Selenium/JSPageMiddleware：
   请求带 meta={"playwright": True} 即可，无需自己管浏览器进程。
2. 选择器快路径 + LLM 自愈兜底 —— 取代"只写死 XPath/CSS"：
   选择器抓空时把清洗后的正文交给 LLM 按 schema 抽取，爬虫不再因改版而断。
   （大规模生产中 LLM 只做兜底和小流量页面，核心字段事后固化回选择器。）
3. 指纹头/住宅代理中间件 —— 由 settings 全局启用，对爬虫透明。

试跑：
    scrapy crawl quotes_ai                       # 静态页，选择器路径
    scrapy crawl quotes_ai -a js=1               # 追加 JS 渲染页（需 playwright install chromium）
    export AI_LLM_API_KEY=你的deepseek-key
    scrapy crawl quotes_ai --set SELECTORS_DISABLED=1   # 模拟改版：选择器全部抓空，观察 LLM 兜底
"""
import scrapy
from scrapy_playwright.page import PageMethod

from common.llm_client import available
from crawler.ai.llm_extract import extract_fields

QUOTE_SCHEMA = {
    "quotes": "list[dict]，每个 dict 含 text(str 名言内容)、author(str 作者)、tags(list[str] 主题标签)",
}


class QuotesAiSpider(scrapy.Spider):
    name = "quotes_ai"
    allowed_domains = ["quotes.toscrape.com"]

    custom_settings = {
        # 演示用限速放宽；真实站点请保持礼貌的并发与延迟
        "DOWNLOAD_DELAY": 1,
    }

    def __init__(self, js=0, pages=3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.render_js = bool(int(js))
        self.max_list_pages = int(pages)
        self._pages_seen = 0

    @property
    def selectors_disabled(self):
        # settings 在 from_crawler 之后才挂载到 spider 实例，需惰性读取
        return self.settings.getbool("SELECTORS_DISABLED")

    async def start(self):
        # Scrapy 2.13+ 的起始请求入口（取代旧的 start_requests）
        yield scrapy.Request(
            "https://quotes.toscrape.com/",
            callback=self.parse_list,
            # 静态页不需要渲染，走普通下载路径
        )
        if self.render_js:
            yield scrapy.Request(
                "https://quotes.toscrape.com/js/",
                callback=self.parse_list,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                    ],
                },
            )

    def parse_list(self, response):
        rendered = "playwright" in response.meta
        self._pages_seen += 1
        quotes = [] if self.selectors_disabled else self._extract_with_selectors(response)

        if quotes:
            self.crawler.stats.inc_value("extract/selectors_ok")
        elif available():
            # ---- LLM 自愈兜底：选择器抓空（或被模拟改版）时 ----
            self.crawler.stats.inc_value("extract/llm_fallback")
            quotes = self._extract_with_llm(response)

        for q in quotes:
            q["rendered_by"] = "playwright" if rendered else "http"
            q["page_url"] = response.url
            yield q

        next_href = None if self.selectors_disabled else response.css("li.next a::attr(href)").extract_first()
        if next_href and self._pages_seen < self.max_list_pages:
            meta = {"playwright": True, "playwright_page_methods": [
                PageMethod("wait_for_load_state", "networkidle")]} if rendered else {}
            yield response.follow(next_href, callback=self.parse_list, meta=meta)

    def _extract_with_selectors(self, response):
        out = []
        for block in response.css("div.quote"):
            out.append({
                "text": block.css("span.text::text").extract_first("").strip("“”"),
                "author": block.css("small.author::text").extract_first(""),
                "tags": block.css("a.tag::text").extract(),
            })
        return [q for q in out if q["text"] or q["author"]]

    def _extract_with_llm(self, response):
        result = extract_fields(
            response.text,
            schema=QUOTE_SCHEMA,
            url=response.url,
            extra_hint="页面是一个名言列表，尽量完整抽取每一条。",
        )
        quotes = (result or {}).get("quotes") or []
        self.logger.info("LLM 兜底抽取到 %d 条 (url=%s)", len(quotes), response.url)
        return [q for q in quotes if isinstance(q, dict) and q.get("text")]
