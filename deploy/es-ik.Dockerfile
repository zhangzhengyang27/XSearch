# XSearch 专用 Elasticsearch：官方 8.15.3 + IK 中文分词插件
# mapping（crawler/pipelines.py QuoteDocument）依赖 ik_max_word / ik_smart 分析器，
# 共享的 dev-es 未装插件，故本项目自带带 IK 的独立 ES（数据卷 ./data/es）。
#
# NAS 构建（必须 legacy builder，见各部署手册）：
#   DOCKER_BUILDKIT=0 docker build -f deploy/es-ik.Dockerfile -t xsearch-es-ik:8.15.3 .
#
# 插件走 infini.cloud 国内源（GitHub 直连在 NAS 上不可靠）；
# 版本必须与基础镜像严格一致，否则 ES 拒绝加载插件。
# 基础镜像选 8.15.3：与 NAS dev-es 同版本（基础镜像已存在，免 1.3GB 拉取）。
FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.3

RUN bin/elasticsearch-plugin install --batch \
    https://get.infini.cloud/elasticsearch/analysis-ik/8.15.3
