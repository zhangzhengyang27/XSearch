# -*- coding: utf-8 -*-
"""
已迁移：索引 mapping 统一由爬虫侧维护（crawler/pipelines.py
的 QuoteDocument，含 suggest 补全字段），搜索/建议接口直接使用原生 ES 客户端。

保留本文件仅为 Django app 惯例；如需 Django admin 管理索引数据，可在此定义
elasticsearch-dsl Document 并注册到 admin。
"""
