# -*- coding: utf-8 -*-
"""
AI 大模型工具包（2026 年爬虫的常用手段）。

- llm_client : OpenAI 兼容协议客户端，默认 DeepSeek
               （deepseek-v4-flash 文本 / deepseek-v4-flash-vision-exp 视觉）
- vlm_captcha: 用视觉大模型识别滑块/点选验证码（替代 OpenCV 模板匹配）
- llm_extract: LLM 结构化抽取（选择器失效时的"自愈"兜底，替代只写死 XPath/CSS）
- fingerprint: 真实浏览器指纹请求头（替代只换 UA 的老做法）

所有能力通过环境变量配置，不设置 AI_LLM_API_KEY 时自动降级，不影响纯选择器路线的爬取。
"""
