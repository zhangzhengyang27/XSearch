# -*- coding: utf-8 -*-
"""
LLM 结构化抽取：选择器（XPath/CSS）失效时的"自愈"兜底。

行业现状的用法是混合模式：
- 大流量页面的核心字段仍用选择器——最快、最便宜、最稳定；
- 选择器抓空（网站改版/长尾页面）时，把清洗后的文本交给 LLM 按 schema 抽取，
  并打统计点，方便事后把新选择器固化回代码。

环境变量同 llm_client；未配置 key 时 extract_fields 返回 None，调用方自然降级。
"""
import json
import logging

from lxml import html as lhtml

from common.llm_client import available, chat_json

logger = logging.getLogger(__name__)

# 单次喂给 LLM 的正文上限（字符），防止超出上下文和浪费 token
MAX_TEXT_CHARS = 8000


def html_to_clean_text(html_content):
    """去掉 script/style 等噪音，保留主体文本。比喂原始 HTML 省 5-10 倍 token。"""
    if not html_content:
        return ""
    doc = lhtml.fromstring(html_content)
    for bad in doc.xpath("//script|//style|//noscript|//comment()"):
        bad.getparent().remove(bad)
    text = doc.text_content()
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)[:MAX_TEXT_CHARS]


def extract_fields(html_content, schema, url=None, extra_hint=""):
    """按 schema 从页面抽取字段。

    :param schema: {"title": "str 文章标题", "tags": "list[str]"} 值写人话描述即可
    :return: dict；LLM 未配置或抽取失败返回 None（调用方应走降级路径）
    """
    if not available():
        return None

    text = html_to_clean_text(html_content)
    if not text:
        return None

    prompt = (
        "你是网页信息抽取助手。下面是一个网页{}的正文文本，"
        "请严格按照字段约定抽取信息。\n\n{}".format(
            "({})".format(url) if url else "", text)
    )
    if extra_hint:
        prompt += "\n\n备注：{}".format(extra_hint)

    try:
        result = chat_json(prompt, schema_hint=schema)
    except Exception as e:
        logger.warning("LLM 抽取失败: %s", e)
        return None

    logger.info("LLM 抽取结果: %s", json.dumps(result, ensure_ascii=False)[:300])
    return result
