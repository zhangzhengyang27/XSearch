# -*- coding: utf-8 -*-
"""
真实浏览器指纹请求头。

老课程只换 User-Agent（fake-useragent），但 2025 年后的风控看的是整组信号：
UA 与 sec-ch-ua 客户端提示要自洽、Accept-Language/Accept 要像真浏览器、
底层还有 TLS 指纹（requests/httpx 的握手特征本身就是 bot 特征，
脱离 Scrapy 单独请求时应改用 curl_cffi impersonate）。

优先用 browserforge（Camoufox 同源的指纹库）生成"成套"的请求头；
未安装时降级为内置的近期 Chrome 指纹模板。
"""
import random

# 降级模板：Chrome 124 风格的完整头组，各字段相互自洽
_FALLBACK_PROFILES = [
    {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    },
    {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
        "sec-ch-ua": '"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="99"',
    },
]
_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
           "image/avif,image/webp,image/apng,*/*;q=0.8,"
           "application/signed-exchange;v=b3;q=0.7")
_LANGS = ["zh-CN,zh;q=0.9,en;q=0.8", "en-US,en;q=0.9", "zh-CN,zh;q=0.9"]

try:
    from browserforge.headers import Browser, HeaderGenerator
    _gen = HeaderGenerator(browser=Browser(name="chrome"))
except Exception:  # ImportError 或初始化失败
    _gen = None


def get_headers():
    """返回一组自洽的浏览器请求头（含 UA、客户端提示、Accept 等）。"""
    if _gen is not None:
        return dict(_gen.generate())

    profile = dict(random.choice(_FALLBACK_PROFILES))
    profile.update({
        "Accept": _ACCEPT,
        "Accept-Language": random.choice(_LANGS),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"' if "Macintosh" in profile["User-Agent"] else '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    })
    return profile
