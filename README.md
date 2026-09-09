# LcvSearch — AI 时代的分布式爬虫与搜索引擎

基于 Scrapy + Elasticsearch + Django + Vue3 的全栈搜索引擎项目，覆盖爬虫采集、数据存储、关键词搜索、爬虫管理等完整链路。

> 本项目源自慕课网《新版 Scrapy 打造搜索引擎》课程，已全面升级为 2026 年 AI 时代技术栈。

## 技术栈

| 层级 | 技术 |
|---|---|
| **爬虫** | Scrapy 2.13+ / scrapy-playwright 动态渲染 / browserforge 指纹伪装 / DeepSeek LLM 自愈抽取 |
| **存储** | Elasticsearch 8（全文检索）/ Redis（缓存 + 去重）/ SQLite（任务记录） |
| **后端** | Django 4.2+ / Django REST 风格 API / APScheduler 定时任务 |
| **前端** | Vue 3 / Vite 6 / Element Plus / Vue Router |

## 功能特性

### 🔍 搜索
- 多源 Tab 切换（B站视频 / 豆瓣电影 / 豆瓣图书 / 掘金文章 / 网易云音乐 / 全网实时聚合）
- 搜索建议（ES completion）
- 分页 + 空页自动回退
- 搜索结果 Redis 缓存 + 热搜词统计

### 🕷️ 爬虫管理
- 7 个内置爬虫（见下表）
- Web 界面一键启动 / 状态监控 / 历史记录
- 定时任务（APScheduler，cron 表达式）
- 中断恢复（Scrapy JOBDIR）
- 采集统计图表

### 📊 榜单
- 抖音热点榜（50 条，实时）
- B站热门 / 每周必看
- 豆瓣电影 Top250 / 豆瓣图书 Top250
- 图片本地磁盘缓存（快 70 倍）

## 项目结构

```
coding-92/
├── LcvSearch/                    # Django 后端 + Scrapy 爬虫（合并后统一项目）
│   ├── LcvSearch/                # Django 配置（settings / urls / wsgi）
│   ├── search/                   # 搜索 + 爬虫管理 API
│   │   ├── api_views.py          # 全部 API 接口
│   │   ├── crawl_manager.py      # 爬虫进程管理 + 定时任务
│   │   ├── live_sources.py       # 实时搜索源（B站/掘金）
│   │   └── models.py             # Django 模型
│   ├── crawler/                  # Scrapy 爬虫（原 ArticleSpider，已合并）
│   │   ├── spiders/              # 7 个爬虫
│   │   ├── ai/                   # LLM 自愈抽取 + VLM 验证码
│   │   ├── tools/                # 工具脚本（知乎登录等）
│   │   ├── middlewares.py        # 指纹伪装 + 代理 + Playwright 降级
│   │   ├── pipelines.py          # ES 入库管道
│   │   └── settings.py           # Scrapy 配置
│   ├── common/                   # 公共模块
│   │   └── llm_client.py         # 统一 LLM 客户端（DeepSeek 兼容）
│   ├── cache/images/             # 图片磁盘缓存
│   ├── jobs/                     # Scrapy JOBDIR（中断恢复）
│   ├── logs/                     # 爬虫日志
│   ├── manage.py                 # Django 入口
│   ├── requirements.txt          # Python 依赖
│   └── scrapy.cfg                # Scrapy 配置
├── frontend/                     # Vue3 前端
│   ├── src/
│   │   ├── views/                # 4 个页面
│   │   │   ├── SearchView.vue    # 搜索页
│   │   │   ├── CrawlView.vue     # 爬虫管理页
│   │   │   ├── RankingsView.vue  # 榜单页
│   │   │   └── StatsView.vue     # 数据统计页
│   │   ├── api.js                # API 封装
│   │   ├── App.vue               # 根组件 + 路由
│   │   └── main.js               # 入口
│   ├── package.json
│   └── vite.config.js
├── docker-compose.yml            # Docker 部署（含 ES/Redis/后端/前端）
├── .env.example                  # 环境变量模板
├── AI升级说明.md                  # 技术栈详细说明
└── README.md                     # 本文件
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- Node.js 18+
- Elasticsearch 8.x
- Redis 6+

### 2. 启动依赖服务

```bash
# 方式一：Docker（推荐）
docker-compose up -d elasticsearch redis

# 方式二：本地安装
# Elasticsearch: https://www.elastic.co/downloads/elasticsearch
# Redis: brew install redis && redis-server
```

### 3. 启动后端

```bash
cd LcvSearch
pip install -r requirements.txt
playwright install chromium   # 首次需要安装浏览器

# 配置环境变量（可选，不配置用默认值）
cp ../.env.example .env
# 编辑 .env，填入 AI_LLM_API_KEY 等

python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173
```

### 5. 采集数据

```bash
cd LcvSearch

# 采集演示站数据（10条）
scrapy crawl quotes_ai -a pages=1

# 采集 B站热门
scrapy crawl bilibili_hot

# 采集豆瓣电影 Top250
scrapy crawl douban_top250 -a kind=movie

# 采集豆瓣图书 Top250
scrapy crawl douban_top250 -a kind=book

# 采集抖音热点榜
scrapy crawl douyin_hot
```

也可以在前端「爬虫管理」页面一键启动。

## 爬虫列表

| 爬虫名 | 数据源 | 说明 |
|---|---|---|
| `quotes_ai` | quotes.toscrape.com | 演示站：选择器快路径 + LLM 自愈兜底 + Playwright 渲染 |
| `bilibili_hot` | B站 | 每日热门 / 全站排行榜 / 入站必刷（公开 API） |
| `bilibili_weekly` | B站 | 「每周必看」官方 series 接口 |
| `bilibili_comments` | B站 | 视频评论（reply/main 游标翻页；配 BILI_COOKIE 抓全量） |
| `douban_top250` | 豆瓣 | 电影 / 图书 Top250（评分/金句/排名入 ES） |
| `news_rss` | 人民网 / 中新网 / IT之家 / Solidot | 新闻 RSS 聚合（官方源合规抓取，中新网自动跟进文章页抓正文；`-a sources=` 可选源） |
| `douyin_hot` | 抖音 | 热点榜 50 条（Playwright 渲染 + 文本解析） |

## API 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/search/` | GET | 关键词搜索（支持 source 筛选、分页） |
| `/api/suggest/` | GET | 搜索建议 |
| `/api/stats/` | GET | 数据概览统计 |
| `/api/rankings/` | GET | 榜单数据（5 个来源） |
| `/api/crawl/start/` | POST | 启动爬虫 |
| `/api/crawl/status/` | GET | 爬虫运行状态 |
| `/api/crawl/history/` | GET | 爬虫历史记录 |
| `/api/crawl/stats/` | GET | 爬虫统计图表 |
| `/api/crawl/resumable/` | GET | 可恢复的中断任务 |
| `/api/crawl/spiders/` | GET | 可用爬虫列表 |
| `/api/crawl/schedule/` | GET/POST | 定时任务管理 |
| `/api/img/` | GET | 图片代理（本地磁盘缓存） |
| `/api/comments/` | GET | B站评论列表 |
| `/api/comments/fetch/` | POST | 触发评论采集 |

## 环境变量

复制 `.env.example` 为 `.env`，按需配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DJANGO_SECRET_KEY` | （内置默认） | Django 密钥，生产环境务必修改 |
| `DJANGO_DEBUG` | `True` | 调试模式，生产环境设为 `False` |
| `DJANGO_ALLOWED_HOSTS` | （空） | 允许的主机名，逗号分隔 |
| `ES_URL` | `http://127.0.0.1:9200` | Elasticsearch 地址（Django 与 Scrapy 统一使用；Docker 部署为 `http://elasticsearch:9200`） |
| `ES_INDEX` | `quotes` | 文档索引名 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接 |
| `AI_LLM_API_KEY` | （空） | DeepSeek API Key（爬虫 LLM 自愈抽取 / 验证码识别需要，不配置自动降级） |
| `AI_LLM_BASE_URL` | `https://api.deepseek.com` | LLM API 地址 |
| `AI_LLM_MODEL` | `deepseek-v4-flash` | LLM 模型 |
| `BILI_COOKIE` | （空） | B站登录 Cookie（评论全量采集需要） |
| `AI_PROXIES` | （空） | 住宅代理列表（逗号分隔） |
| `API_TOKEN` | （空） | 设置后写操作与 AI 问答接口要求 `X-API-Token` 请求头（留空不启用） |

## Docker 部署

```bash
# 一键启动全部服务（ES + Redis + 后端 + 前端）
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 采集数据
docker-compose exec backend scrapy crawl bilibili_hot
```

访问：
- 前端：http://localhost:8080
- 后端 API：http://localhost:8000
- Elasticsearch：http://localhost:9200

## 测试

```bash
cd LcvSearch
python manage.py test search --verbosity=2
```

9 个单元测试覆盖：API 参数校验、CORS 中间件、ES 异常降级等。

## 许可证

MIT
