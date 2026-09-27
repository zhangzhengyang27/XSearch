# -*- coding: utf-8 -*-
"""
下载中间件（AI 时代方案）。

- BrowserFingerprintHeadersMiddleware : 整组自洽浏览器指纹头（仅 FINGERPRINT_HOSTS 域名）
- ResidentialProxyMiddleware          : 住宅代理轮换（数据中心 IP 裸奔即被封）
- PlaywrightFallbackMiddleware        : 渲染失败自动降级直连

动态页面渲染不在这里做，走 scrapy-playwright：
请求 meta={"playwright": True} 即可，见 spiders/douyin_hot.py。
"""
import itertools
import logging
import os
from urllib.parse import urlparse

from crawler.ai.fingerprint import get_headers

logger = logging.getLogger(__name__)


class BrowserFingerprintHeadersMiddleware(object):
    """对白名单域名替换成成套的真实浏览器指纹头，其余域名保留自报身份的 USER_AGENT。

    风控会校验 UA / sec-ch-ua / sec-ch-ua-platform / Accept-Language 是否自洽，
    单独换 UA 反而是明显的爬虫特征。优先 browserforge，未安装则用内置模板。

    把伪装限制在白名单域名内，是为了不让"伪装浏览器"成为全站默认行为：
    那既扩大合规风险，也让站方无法按 UA 放行或精准封禁。
    """

    def __init__(self, hosts=()):
        self._hosts = tuple((h or "").lstrip(".").lower() for h in hosts if h)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.getlist("FINGERPRINT_HOSTS"))

    def process_request(self, request):
        host = (urlparse(request.url).hostname or "").lower()
        if not self._matches(host):
            return None
        # 直接替换（而非 setdefault）：内置 UserAgentMiddleware（优先级 500）已先写入
        # USER_AGENT，setdefault 不会覆盖它，真实指纹头就不生效（曾导致 B站 接口 412）
        try:
            headers = get_headers()
        except Exception as e:
            logger.warning("指纹生成失败（%s），使用上一次/内置头", e)
            return None
        for k, v in headers.items():
            request.headers[k] = v
        return None

    def _matches(self, host):
        """精确匹配域名或其子域，避免 "evil-douyin.com" 命中 "douyin.com" 这类后缀假阳性。"""
        return any(host == h or host.endswith("." + h) for h in self._hosts)


class ResidentialProxyMiddleware(object):
    """住宅代理轮换。

    主流住宅/ISP 代理（Bright Data、IPRoyal、快代理动态私密版等）用标准认证，
    直接把用户名密码写在 URL 里即可，不需要隧道 host + Proxy-Authorization 头。

    配置（轮换使用；都未配置则直连）：
        settings: RESIDENTIAL_PROXIES = ["http://user:pass@gate.x.com:port", ...]
        环境变量: AI_PROXIES="http://user:pass@host:port,http://user2:pass2@host2:port"
    """

    def __init__(self, proxies):
        self._pool = itertools.cycle(proxies) if proxies else None

    @classmethod
    def from_crawler(cls, crawler):
        proxies = (crawler.settings.getlist("RESIDENTIAL_PROXIES")
                   or [p.strip() for p in os.getenv("AI_PROXIES", "").split(",") if p.strip()])
        return cls(proxies)

    def process_request(self, request):
        if self._pool:
            request.meta["proxy"] = next(self._pool)


class PlaywrightFallbackMiddleware(object):
    """scrapy-playwright 渲染失败（超时/崩溃）时降级为普通直连请求，不让爬虫整体中断。"""

    def process_exception(self, request, exception):
        if request.meta.get("playwright"):
            logger.warning("playwright 渲染失败，降级直连: %s (%s)", request.url, exception)
            # 仅移除 playwright 标记，保留 download_slot 等其他 meta——
            # 不移除 download_slot，避免绕过对目标站的礼貌限速（DOWNLOAD_DELAY/AUTOTHROTTLE）
            retry_meta = {k: v for k, v in request.meta.items() if k != "playwright"}
            return request.replace(meta=retry_meta, dont_filter=True)
        return None
