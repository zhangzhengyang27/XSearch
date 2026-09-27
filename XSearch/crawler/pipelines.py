# -*- coding: utf-8 -*-
"""
把爬取结果写入 Elasticsearch（默认开启）。

数据流：news_rss / aihot_hot 等爬虫 -> 本管道 -> ES "quotes" 索引
        -> XSearch 关键词搜索（/search/）等 API 复用

启动时探测 ES：不可用则告警并跳过入库，不影响爬虫运行（大规模生产建议改批量写入）。
"""
import datetime
import hashlib
import logging
import os

from elasticsearch_dsl import (
    Document, Text, Keyword, Date, Integer, Float, Completion,
    analyzer, token_filter, connections,
)

logger = logging.getLogger(__name__)


def _default_es_url():
    """模块级默认连接的地址：环境变量 ES_URL 优先，其次 Django settings（实时搜索
    回写 ES 走这条默认连接，必须与 api_views 的读连接同源），最后回退本机。"""
    url = os.getenv("ES_URL")
    if url:
        return url
    try:
        from django.conf import settings
        return getattr(settings, "ES_URL", "http://127.0.0.1:9200")
    except Exception:
        return "http://127.0.0.1:9200"


# 模块级注册默认连接：本模块被两个进程复用（爬虫进程 open_spider 会按 settings
# 重新注册；/api/ai/item 等直接使用原生客户端，不经此默认连接）。
# 缺失时 dsl 报 "no connection with alias 'default'"（旧版 models.py 承担此职责）。
# 加 try-except 保护：ES 不可用时不阻塞模块导入，由调用方自行降级。
try:
    connections.create_connection(hosts=[_default_es_url()], timeout=10)
except Exception as _e:
    logger.warning("模块级 ES 连接注册失败（%s），将在 open_spider 中重试", _e)

# 检索侧停用词：IK 会把"的/了/是"切成独立词元，OR 语义下命中几乎所有中文文档，
# 造成"搜肖申克的救赎、出来沉默的羔羊"的噪音（词表按需扩充）
CN_STOPWORDS = ["的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
                "一", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
                "没有", "看", "好", "这", "那", "与", "及", "或", "等", "之",
                "吗", "吧", "呢", "啊", "把", "被", "让", "给", "对", "向",
                "以", "为", "从", "其", "它", "他", "她", "们"]

ik_stop_filter = token_filter("ik_cn_stop", type="stop", stopwords=CN_STOPWORDS)
# 检索侧：ik_smart 粗分词 + 停用词过滤（建索侧保持 ik_max_word 细粒度）
ik_search_analyzer = analyzer("ik_smart_stop", tokenizer="ik_smart",
                              filter=["lowercase", ik_stop_filter])


class QuoteDocument(Document):
    """通用文章/视频文档：字段与 XSearch 的检索面兼容。

    中文文本字段使用 IK 分词（需 ES 安装 analysis-ik 插件）：
    建索引用 ik_max_word 细粒度，检索用 ik_smart + 停用词——中文搜索的标准组合，
    默认 standard 分词会把标题按单字切分，导致单字命中噪音淹没精确匹配。
    """
    suggest = Completion()
    title = Text(analyzer="ik_max_word", search_analyzer=ik_search_analyzer)
    content = Text(analyzer="ik_max_word", search_analyzer=ik_search_analyzer)
    tags = Text(analyzer="ik_max_word", search_analyzer=ik_search_analyzer)
    author = Keyword()
    url = Keyword()
    url_object_id = Keyword()  # url 的 md5 或站点原生唯一 ID，同时作为 ES 文档 _id 去重
    front_image_url = Keyword()
    rating = Float()           # 评分（豆瓣等）
    praise_nums = Integer()    # 点赞
    view_nums = Integer()      # 播放
    reply_nums = Integer()     # 评论
    danmaku_nums = Integer()   # 弹幕
    coin_nums = Integer()      # 投币
    favorite_nums = Integer()  # 收藏
    share_nums = Integer()     # 分享
    duration = Integer()       # 秒
    rank = Integer()           # 站内榜单排名
    source = Keyword()
    crawled_at = Date()
    create_date = Date()       # 内容发布时间（有则存）

    class Index:
        name = "quotes"


def utc_now():
    """采集时刻，一律带时区（UTC）。

    naive 的 datetime.now() 会被 ES 当作 UTC 解释；容器里 TZ=Asia/Shanghai，
    于是每条文档的时间都往前推了 8 小时——"近 7 天"分面与按时间排序都会错位。
    写入统一 aware UTC，展示侧再按站点时区换算（见 search/api_views._display_date）。
    """
    return datetime.datetime.now(datetime.timezone.utc)


def _to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class EsArticlePipeline(object):
    def __init__(self, hosts, index):
        self.hosts = hosts
        self.index = index
        self.doc_cls = None
        self.crawler = None

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls(
            hosts=crawler.settings.getlist("ES_HOSTS") or ["http://127.0.0.1:9200"],
            index=crawler.settings.get("ES_INDEX", "quotes"),
        )
        pipeline.crawler = crawler
        return pipeline

    def open_spider(self, spider=None):
        try:
            es = connections.create_connection(hosts=self.hosts, request_timeout=5)
            es.info()
            QuoteDocument._index._name = self.index
            QuoteDocument.init()
            self.doc_cls = QuoteDocument
        except Exception as e:
            logger.warning("ES 不可用（%s），本次爬取数据将不入库", e)

    # spider 参数改为可选：Scrapy 2.18 起弃用"必须接收 spider"的旧签名，
    # 未来版本不再透传该参数（spider 名可经 crawler.spider 属性获取）
    def process_item(self, item, spider=None):
        if self.doc_cls is None:
            return item
        if index_item(item, source=item.get("source") or (
                self.crawler.spider.name if self.crawler and self.crawler.spider else None)):
            self.crawler.stats.inc_value("es_saved")
        else:
            self.crawler.stats.inc_value("es_skipped")
        return item


def index_item(item, source=None):
    """把标准化 item 写入 ES。失败返回 False。"""
    content = item.get("text") or item.get("content") or ""
    if not content:
        return False

    url = item.get("url") or item.get("page_url") or ""
    title = item.get("title") or item.get("author") or content[:40]
    doc = QuoteDocument(
        suggest={"input": [title, item.get("author") or ""]},
        title=title,
        content=content,
        author=item.get("author"),
        tags=item.get("tags") or [],
        url=url,
        url_object_id=item.get("url_object_id"),
        front_image_url=item.get("front_image_url") or "",
        rating=_to_float(item.get("rating")),
        praise_nums=_to_int(item.get("praise_nums")),
        view_nums=_to_int(item.get("view_nums")),
        reply_nums=_to_int(item.get("reply_nums")),
        danmaku_nums=_to_int(item.get("danmaku_nums")),
        coin_nums=_to_int(item.get("coin_nums")),
        favorite_nums=_to_int(item.get("favorite_nums")),
        share_nums=_to_int(item.get("share_nums")),
        duration=_to_int(item.get("duration")),
        rank=_to_int(item.get("rank")),
        source=item.get("source") or source,
        create_date=item.get("create_date"),
        crawled_at=utc_now(),
    )
    doc.meta.id = item.get("url_object_id") or _md5(url + content[:200])
    try:
        doc.save()
        return True
    except Exception as e:
        logger.warning("写入 ES 失败（%s）: %s", e, url)
        return False


def _md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()
