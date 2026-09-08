# -*- coding: utf-8 -*-
"""
统一的大模型客户端（合并自 ArticleSpider 和 LcvSearch 两个项目的 llm_client.py）。

OpenAI 兼容协议，requests 直连，零 SDK 依赖。默认 DeepSeek。

    问答生成（deepseek-v4-flash）：
        export AI_LLM_API_KEY="your-deepseek-key"
        # AI_LLM_BASE_URL 默认 https://api.deepseek.com
        # AI_LLM_MODEL   默认 deepseek-v4-flash

    视觉/验证码识别（deepseek-v4-flash-vision-exp）：
        # AI_VLM_BASE_URL 默认同 AI_LLM_BASE_URL
        # AI_VLM_API_KEY  默认同 AI_LLM_API_KEY
        # AI_VLM_MODEL    默认 deepseek-v4-flash-vision-exp

    向量化（DeepSeek 平台不提供 embedding 接口，默认指向智谱 embedding-3）：
        export AI_EMBED_API_KEY="your-zhipu-key"
        # AI_EMBED_BASE_URL 默认 https://open.bigmodel.cn/api/paas/v4
        # AI_EMBED_MODEL   默认 embedding-3
        可换成任何实现了 OpenAI /embeddings 协议的服务商。
"""
import json
import os
import re

import requests

DEFAULT_TIMEOUT = 60


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------- 配置读取
def _cfg(env_prefix="AI_LLM", default_model="deepseek-v4-flash"):
    return {
        "base_url": os.getenv(env_prefix + "_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        "api_key": os.getenv(env_prefix + "_API_KEY", ""),
        "model": os.getenv(env_prefix + "_MODEL", default_model),
    }


def _llm_cfg():
    return _cfg("AI_LLM", "deepseek-v4-flash")


def _vlm_cfg():
    cfg = _cfg("AI_VLM", "deepseek-v4-flash-vision-exp")
    if "AI_VLM_BASE_URL" not in os.environ:
        cfg["base_url"] = os.getenv("AI_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if "AI_VLM_API_KEY" not in os.environ:
        cfg["api_key"] = os.getenv("AI_LLM_API_KEY", "")
    return cfg


def _embed_cfg():
    return {
        "base_url": os.getenv("AI_EMBED_BASE_URL",
                              "https://open.bigmodel.cn/api/paas/v4").rstrip("/"),
        "api_key": os.getenv("AI_EMBED_API_KEY", ""),
        "model": os.getenv("AI_EMBED_MODEL", "embedding-3"),
    }


# ---------------------------------------------------------------- 可用性检查
def llm_available():
    """是否配置了可用的 LLM 服务。"""
    return bool(_llm_cfg()["api_key"])


def embed_available():
    """是否配置了可用的 embedding 服务。"""
    return bool(_embed_cfg()["api_key"])


def available():
    """兼容旧接口：同 llm_available()。"""
    return llm_available()


# ---------------------------------------------------------------- 底层请求
def _post(base_url, api_key, payload, timeout):
    resp = requests.post(
        "{}/chat/completions".format(base_url),
        headers={"Authorization": "Bearer {}".format(api_key),
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise LLMError("LLM 接口返回 {}: {}".format(resp.status_code, resp.text[:300]))
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError("LLM 响应格式异常: {}".format(json.dumps(data)[:300]))


# ---------------------------------------------------------------- 问答生成
def chat(messages, model=None, temperature=0.2, max_tokens=2048, timeout=DEFAULT_TIMEOUT,
         images_b64=None):
    """调用 /chat/completions，返回模型回复文本。

    :param model:       覆盖默认模型（None 时用配置中的模型）
    :param images_b64:  可选，base64 图片列表（不带 data: 前缀）；传入时自动切换到 VLM 配置
    """
    cfg = _vlm_cfg() if images_b64 else _llm_cfg()
    if not cfg["api_key"]:
        raise LLMError("未配置 AI_LLM_API_KEY，无法调用 LLM")

    if images_b64:
        text_msg = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        messages = messages[:-1] + [{
            "role": "user",
            "content": [{"type": "text", "text": text_msg}]
            + [{"type": "image_url", "image_url": {"url": "data:image/png;base64,{}".format(b)}}
               for b in images_b64],
        }]

    return _post(cfg["base_url"], cfg["api_key"], {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }, timeout)


# ---------------------------------------------------------------- 流式问答生成
def chat_stream(messages, model=None, temperature=0.2, max_tokens=2048,
                timeout=DEFAULT_TIMEOUT, images_b64=None):
    """流式调用 /chat/completions，返回生成器，逐个 yield 文本片段。

    用于 SSE 流式输出，用户可以看到回答逐字出现。
    用法：
        for chunk in chat_stream(messages):
            print(chunk, end="", flush=True)
    """
    cfg = _vlm_cfg() if images_b64 else _llm_cfg()
    if not cfg["api_key"]:
        raise LLMError("未配置 AI_LLM_API_KEY，无法调用 LLM")

    if images_b64:
        text_msg = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        messages = messages[:-1] + [{
            "role": "user",
            "content": [{"type": "text", "text": text_msg}]
            + [{"type": "image_url", "image_url": {"url": "data:image/png;base64,{}".format(b)}}
               for b in images_b64],
        }]

    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    resp = requests.post(
        "{}/chat/completions".format(cfg["base_url"]),
        headers={"Authorization": "Bearer {}".format(cfg["api_key"]),
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
        stream=True,
    )
    if resp.status_code != 200:
        raise LLMError("LLM 流式接口返回 {}: {}".format(resp.status_code, resp.text[:300]))

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
            delta = obj["choices"][0]["delta"].get("content", "")
            if delta:
                yield delta
        except (json.JSONDecodeError, KeyError, IndexError):
            continue


# ---------------------------------------------------------------- JSON 结构化输出
def chat_json(prompt, schema_hint=None, images_b64=None, model=None, temperature=0.0,
              max_tokens=2048, timeout=DEFAULT_TIMEOUT):
    """让模型返回 JSON 并解析。失败自动重试一次。

    :param schema_hint: 形如 {"title": "str", "tags": "list[str]"} 的字段说明，会拼进提示词
    :param images_b64:  可选，base64 图片列表，自动走视觉模型配置
    :return: 解析后的 dict/list；解析失败抛 LLMError
    """
    text = prompt
    if schema_hint:
        text += "\n\n返回 JSON，字段约定：{}".format(json.dumps(schema_hint, ensure_ascii=False))

    messages = [{"role": "user", "content": text}]

    last_err = None
    for _ in range(2):
        reply = chat(messages, model=model, temperature=temperature,
                     max_tokens=max_tokens, timeout=timeout, images_b64=images_b64)
        try:
            return _loads_loose(reply)
        except ValueError as e:
            last_err = e
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": "上面不是合法 JSON，只输出 JSON 本身，不要任何多余文字。"})
    raise LLMError("LLM 两次输出均无法解析为 JSON: {}".format(last_err))


def _loads_loose(text):
    """容错解析：剥掉 markdown 代码块、截取最外层 JSON 主体。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            return json.loads(text[start:end + 1])
    raise ValueError("no json found in: {}".format(text[:200]))


# ---------------------------------------------------------------- 向量化
def embed_texts(texts, timeout=DEFAULT_TIMEOUT):
    """批量向量化，返回 list[list[float]]。超过批上限自动分批。"""
    cfg = _embed_cfg()
    if not cfg["api_key"]:
        raise LLMError("未配置 AI_EMBED_API_KEY，无法调用 embedding 服务")
    out = []
    for i in range(0, len(texts), 25):  # 智谱单批上限 64，保守取 25
        batch = [t[:6000] for t in texts[i:i + 25]]  # 按模型上限截断
        resp = requests.post(
            "{}/embeddings".format(cfg["base_url"]),
            headers={"Authorization": "Bearer {}".format(cfg["api_key"]),
                     "Content-Type": "application/json"},
            json={"model": cfg["model"], "input": batch},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = sorted(resp.json()["data"], key=lambda d: d["index"])
        out.extend([d["embedding"] for d in items])
    return out
