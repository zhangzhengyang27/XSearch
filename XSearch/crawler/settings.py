# -*- coding: utf-8 -*-
"""
Scrapy settings for XSearch 爬虫模块（原 ArticleSpider，已合并入 XSearch 项目）。

技术栈：Scrapy 2.13+ / scrapy-playwright / browserforge 指纹 / 住宅代理 /
        DeepSeek LLM（语义抽取自愈 + RAG 问答）
"""
import os
import sys

BOT_NAME = 'XSearchCrawler'

SPIDER_MODULES = ['crawler.spiders']
NEWSPIDER_MODULE = 'crawler.spiders'

ROBOTSTXT_OBEY = False

# 对目标站保持礼貌的并发与延迟（真实站点请按 robots/条款调整）
DOWNLOAD_DELAY = 1
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_MAX_DELAY = 3
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

COOKIES_ENABLED = False

# ---------- 下载中间件（指纹头 / 住宅代理 / Playwright 降级） ----------
DOWNLOADER_MIDDLEWARES = {
    # 内置 UA 中间件会写入 Scrapy 默认 UA，干扰指纹伪装，禁用
    'scrapy.downloadermiddlewares.useragent.UserAgentMiddleware': None,
    'crawler.middlewares.BrowserFingerprintHeadersMiddleware': 543,
    'crawler.middlewares.ResidentialProxyMiddleware': 544,
    'crawler.middlewares.PlaywrightFallbackMiddleware': 545,
}

# ---------- 动态渲染：scrapy-playwright（替代 Selenium） ----------
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 60_000
PLAYWRIGHT_MAX_CONTEXTS = 4
PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = 4

# ---------- 住宅代理 ----------
# 逗号分隔的代理 URL（http://user:pass@host:port），或环境变量 AI_PROXIES
RESIDENTIAL_PROXIES = []

# ---------- AI 大模型（DeepSeek） ----------
# 语义抽取 / RAG 问答走 deepseek-v4-flash；验证码识别走 deepseek-v4-flash-vision-exp。
# 均为 OpenAI 兼容协议，环境变量可覆盖：
#   AI_LLM_API_KEY   DeepSeek API Key（不配置时自动降级为纯选择器路线）
#   AI_LLM_BASE_URL  默认 https://api.deepseek.com
#   AI_LLM_MODEL     默认 deepseek-v4-flash
#   AI_VLM_MODEL     默认 deepseek-v4-flash-vision-exp（DeepSeek 无独立视觉 base_url）

# ---------- Item 管道：写入 Elasticsearch（供搜索站关键词检索 + RAG 索引） ----------
ITEM_PIPELINES = {
    'crawler.pipelines.EsArticlePipeline': 300,
}
# 与 Django 侧统一使用 ES_URL（docker-compose 也注入该变量）；
# 兼容旧的 ES_HOSTS（逗号分隔多节点，优先级更高）
ES_HOSTS = [h.strip() for h in os.getenv(
    "ES_HOSTS", os.getenv("ES_URL", "http://127.0.0.1:9200")).split(",") if h.strip()]
ES_INDEX = os.getenv("ES_INDEX", "quotes")

# 让项目根目录加入 sys.path，使 crawler / common 包可被 Scrapy 导入
# （settings 位于 XSearch/crawler/，根目录是上一级 XSearch/）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
