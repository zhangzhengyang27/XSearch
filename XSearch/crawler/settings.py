# -*- coding: utf-8 -*-
"""
Scrapy settings for XSearch 爬虫模块（原 ArticleSpider，已合并入 XSearch 项目）。

技术栈：Scrapy 2.13+ / scrapy-playwright / browserforge 指纹（仅需要的域名）
"""
import os
import sys

BOT_NAME = 'XSearchCrawler'

SPIDER_MODULES = ['crawler.spiders']
NEWSPIDER_MODULE = 'crawler.spiders'

# 遵守 robots.txt。2026-09-27 逐源核过：中新网 / IT之家 / Solidot 的 User-agent: *
# 组均未禁用 feed 与文章路径，aihot.news 明确「Allow: /api/v1/」，
# 抖音 robots 无 User-agent: * 禁项 —— 现有源全部合规可采。
ROBOTSTXT_OBEY = True

# 对目标站保持礼貌的并发与延迟（真实站点请按 robots/条款调整）
DOWNLOAD_DELAY = 1
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_MAX_DELAY = 3
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

COOKIES_ENABLED = False

# 自报身份：默认所有请求带可联系的产品 UA，便于站方按 UA 白名单放行或精准封禁
USER_AGENT = 'XSearchBot/1.0 (+https://github.com/zhangzhengyang27/XSearch)'

# 需要伪装成浏览器才能取到数据的域名白名单（不在列表里的一律发上面的 USER_AGENT）。
# 目前只有抖音：实测诚实 UA 请求 www.douyin.com/hot 返回 444（nginx 层主动拒绝），
# 其余源无需伪装。新增条目 = 扩大风险面，动手前先确认该站真的拒绝常规 UA。
FINGERPRINT_HOSTS = ['www.douyin.com', 'douyin.com']

# ---------- 下载中间件（指纹头 / 住宅代理 / Playwright 降级） ----------
DOWNLOADER_MIDDLEWARES = {
    # 内置 UA 中间件写入上面的 USER_AGENT；指纹中间件仅对白名单域名整体替换请求头
    # （替换而非 setdefault，见 BrowserFingerprintHeadersMiddleware 注释）
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

# ---------- Item 管道：写入 Elasticsearch（供搜索站关键词检索） ----------
ITEM_PIPELINES = {
    'crawler.pipelines.EsArticlePipeline': 300,
}
# 与 Django 侧统一使用 ES_URL（docker-compose 也注入该变量）；
# 兼容旧的 ES_HOSTS（逗号分隔多节点，优先级更高）
ES_HOSTS = [h.strip() for h in os.getenv(
    "ES_HOSTS", os.getenv("ES_URL", "http://127.0.0.1:9200")).split(",") if h.strip()]
ES_INDEX = os.getenv("ES_INDEX", "quotes")

# 让项目根目录加入 sys.path，使 crawler 包可被 Scrapy 导入
# （settings 位于 XSearch/crawler/，根目录是上一级 XSearch/）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
