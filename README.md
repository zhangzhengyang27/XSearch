# XSearch — 分布式爬虫与全文检索站

基于 Scrapy + Elasticsearch + Django + Vue3 的全栈搜索站，覆盖爬虫采集、数据存储、关键词搜索、采集管理完整链路。

> 本项目源自慕课网《新版 Scrapy 打造搜索引擎》课程，已升级为 2026 年技术栈，并按公开站点的标准做过一轮安全与合规收口（见「发布清单」）。

## 技术栈

| 层级 | 技术 |
|---|---|
| **爬虫** | Scrapy 2.18 / scrapy-playwright 动态渲染 / browserforge 指纹（仅限必要域名）/ 遵守 robots.txt |
| **存储** | Elasticsearch 8.15 + IK 中文分词（全文检索）/ Redis（缓存 + 去重 + 热搜词）/ SQLite（Django 会话） |
| **后端** | Django 6.1 / JSON API / APScheduler 定时任务 / gunicorn（单 worker，见下方说明） |
| **前端** | Vue 3.5 / Vite 6 / Element Plus / TypeScript（`vue-tsc` 严格模式，构建期类型检查） |

## 功能特性

### 🔍 搜索
- 搜索结果实体卡片（新闻来源标签）
- 搜索建议（ES completion）、容错纠错、排序切换、分面过滤
- 分页 + 空页自动回退、空结果热搜词引导
- 搜索结果 Redis 缓存 + 热搜词统计

### 🕷️ 采集管理（需管理员登录）
- 5 个内置爬虫（见「爬虫列表」）
- Web 界面一键启动 / 状态监控 / 历史记录 / 日志尾部
- 定时任务（APScheduler，cron 表达式）
- 中断恢复（Scrapy JOBDIR，任务名经路径校验）
- 采集统计图表

### 📊 榜单与资讯
- AI 热点榜（AIHOT 官方 API，事件 AI 综述）
- 抖音热点榜（50 条，Playwright 渲染）
- 新闻列表（中新网 / IT之家 / Solidot + AIHOT 精选与日报，定时增量）

### 🤖 AI 讯息（aihot.news 官方 API，独立「AI 讯息」导航）
- AI 精选：LLM 摘要 + 0-100 评分 + 入选理由，按 7 天窗口增量入库
- AI 日报：每天 8 点发布的精编日报（头条 + 模型/产品/行业/论文/观点版块），折叠阅读
- AI 热点榜：48 小时多信源印证事件排行（含事件 AI 综述）

> 该数据源对 `User-agent: *` 明确 `Allow: /api/v1/`，本项目只调用 `/api/v1/*` 公开接口。

## 项目结构

```
XSearch/                        # 仓库根
├── XSearch/                    # Django 后端 + Scrapy 爬虫（合并后统一项目）
│   ├── xsearch/                # Django 配置（settings / urls / wsgi / middleware）
│   ├── search/                 # 搜索 + 采集管理应用
│   │   ├── api_views.py        # 全部 JSON API
│   │   ├── crawl_manager.py    # 爬虫子进程管理 + 定时任务
│   │   └── tests.py            # 后端测试（python manage.py test search）
│   ├── crawler/                # Scrapy 爬虫（原 ArticleSpider，已合并）
│   │   ├── spiders/            # 5 个爬虫
│   │   ├── ai/fingerprint.py   # 浏览器指纹头（仅 FINGERPRINT_HOSTS 域名）
│   │   ├── middlewares.py      # 指纹 / 代理 / Playwright 降级
│   │   ├── pipelines.py        # ES 入库管道（含索引 mapping）
│   │   └── settings.py         # Scrapy 配置
│   ├── manage.py
│   ├── requirements.txt        # 依赖范围（开发用）
│   ├── requirements-lock.txt   # 依赖锁定（Docker 构建用）
│   └── scrapy.cfg
├── frontend/                   # Vue3 前端
│   ├── src/
│   │   ├── views/              # 9 个页面组件
│   │   ├── components/         # LoginForm
│   │   ├── api.ts              # 类型化 API 客户端
│   │   ├── auth.ts             # 管理员登录态 + 管理页路由清单
│   │   ├── App.vue             # 根组件（导航）
│   │   └── main.ts             # 入口 + 路由与守卫（共 10 条路由）
│   ├── package.json
│   └── vite.config.ts
├── deploy/                     # es-ik.Dockerfile / nginx.conf
├── docker-compose.yml          # 本机开发部署（ES/Redis 只绑 127.0.0.1）
├── docker-compose.prod.yml     # 生产部署（NAS，公网唯一入口经 frp + VPS Nginx）
├── Dockerfile / frontend.Dockerfile
└── .env.example                # 环境变量模板
```

## 快速开始

### 1. 环境要求

- Python 3.12（`requirements-lock.txt` 锁定的 Django 6.1 要求 3.12+）
- Node.js 18+
- Docker（用于起 Elasticsearch + Redis）

### 2. 启动依赖服务

```bash
# 只起 ES 与 Redis（ES 会现场构建 deploy/es-ik.Dockerfile，内置 IK 分词插件）
docker compose up -d elasticsearch redis
```

> 不要用 stock `elasticsearch:8.x` 镜像：索引 mapping 依赖 `ik_max_word`/`ik_smart`，
> 缺插件时第一次采集会在建索引阶段失败。

### 3. 启动后端

```bash
cd XSearch
pip install -r requirements-lock.txt   # 与 Docker 构建一致；开发也可装 requirements.txt
playwright install chromium            # 仅采集抖音榜需要

python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

本机开发态不需要任何环境变量即可跑通（此时 `DEBUG=True`、使用仅开发态的密钥，
启动日志会给出提示）。**对外提供访问前必须配置环境变量，见「发布清单」。**

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173，Vite 已代理 /api → 8000
npm run build        # 产物 frontend/dist（含 vue-tsc 类型检查）
```

### 5. 采集数据

```bash
cd XSearch

scrapy crawl news_rss        # 新闻 RSS 聚合（中新网 / IT之家 / Solidot）
scrapy crawl aihot_news      # AI 精选 + 当日 AI 日报
scrapy crawl aihot_hot       # AI 热点榜
scrapy crawl news_backfill -a days=180   # 中新网历史回填（幂等，可重复跑）
scrapy crawl douyin_hot      # 抖音热点榜（需 playwright）
```

也可以在前端「采集管理」页面一键启动（需先登录）。

## 爬虫列表

| 爬虫名 | 数据源 | 说明 |
|---|---|---|
| `news_rss` | 中新网 / IT之家 / Solidot | 新闻 RSS 聚合（官方源，中新网自动跟进文章页抓正文；`-a sources=` 可选源） |
| `news_backfill` | 中新网存档 | 历史回填：按日期滚动存档页逐日抓标题（`-a days=180`），幂等可重复跑 |
| `aihot_news` | AIHOT（aihot.news） | AI 精选动态（LLM 摘要+评分+入选理由，7 天窗口）+ 当日 AI 日报；`-a full=1` 拉全量 |
| `aihot_hot` | AIHOT（aihot.news） | AI 热点榜 Top10（官方 v1 API，事件 AI 综述入 ES，跌出榜的条目自动清理） |
| `douyin_hot` | 抖音 | 热点榜 50 条（Playwright 渲染 + 文本解析） |

**已停用**：`news_people`（人民网）。2026-09-27 实测其全部 `/rss/*.xml` 的 pubDate 冻结在
2025-06-05，站方已停止维护 RSS；继续采集只会用 `md5(url)` 反复覆盖同一批一年多前的旧文档。
入库的既有 `news_people` 文档保留，可按需用「数据管理」页清理。

## API 接口

鉴权方式：**公开** / **管理员**（登录接口签发 token，请求头 `X-Admin-Token` 携带）/
**管理员 + API_TOKEN**（`API_TOKEN` 非空时额外要求 `X-API-Token`，面向脚本类非浏览器客户端）。

| 接口 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/` | GET | 公开 | 服务自描述（版本 + 端点清单） |
| `/api/search/` | GET | 公开 | 关键词搜索（`source` 筛选、`sort`、`days`、分页） |
| `/api/suggest/` | GET | 公开 | 搜索建议 |
| `/api/stats/` | GET | 公开 | 数据概览统计 |
| `/api/rankings/` | GET | 公开 | 榜单 / 新闻列表（AI 热点榜、抖音榜 + 新闻各源 + AI 日报） |
| `/api/ai/item/` | GET | 公开 | AI 条目详情（按标题检索本地 ES） |
| `/api/img/` | GET | 公开 | 图片代理（本地磁盘缓存，域名白名单） |
| `/api/auth/login/` | POST | 公开 | 管理员登录；**按 IP 限速**，5 次失败锁 15 分钟 |
| `/api/auth/logout/` | POST | 管理员 | 退出登录（吊销 token） |
| `/api/crawl/start/` | POST | 管理员 | 启动爬虫（拉起子进程，故必须鉴权） |
| `/api/crawl/status/` | GET | 管理员 | 运行状态 + 日志尾部（含服务器绝对路径） |
| `/api/crawl/history/` | GET | 管理员 | 采集历史记录 |
| `/api/crawl/stats/` | GET | 管理员 | 采集统计图表 |
| `/api/crawl/resumable/` | GET | 管理员 | 可恢复的中断任务（JOBDIR 列表） |
| `/api/crawl/spiders/` | GET | 管理员 | 可用爬虫列表（白名单） |
| `/api/crawl/schedule/` | GET | 管理员 | 定时任务列表 |
| `/api/crawl/schedule/{add,remove,update,toggle}/` | POST | 管理员 + API_TOKEN | 定时任务增删改停 |
| `/api/admin/db/overview/` | GET | 管理员 | 索引概览：文档数/大小/来源分布 |
| `/api/admin/db/docs/` | GET | 管理员 | 文档分页浏览（`source`/`q`/`p`） |
| `/api/admin/db/doc/<id>/` | GET/PUT/DELETE | 管理员 | 单文档详情/编辑（字段白名单）/删除 |
| `/api/admin/db/purge/` | POST | 管理员 | 按来源批量清理（`confirm` 逐字确认） |

### 🔐 管理员登录

「采集管理」「数据管理」页仅管理员可用：未登录访问会跳转登录页（`/login`），登录有效期
12 小时（有效期内每次请求滑动续期）。token 存在后端**进程内**，因此后端重启后需重新登录，
也因此 **gunicorn 必须 `--workers 1`**（爬虫互斥锁与定时调度同样是进程内状态）。

管理员账号配置在 `XSearch/local_settings.py`（已加入 `.gitignore`，严禁提交真实账号，格式参考
`local_settings.py.example`），也可用环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD`
（文件配置优先级更高）。口令过弱（长度 <10 或命中常见口令表）会在启动日志中告警。

`/admin/`（Django admin）默认不代理到公网：`deploy/nginx.conf` 只转发 `/api/`。

## 环境变量

复制 `.env.example` 为 `.env`，按需配置。**生产（`DJANGO_DEBUG=False`）下
`DJANGO_SECRET_KEY` 与 `DJANGO_ALLOWED_HOSTS` 缺失时后端会直接拒绝启动**（有意 fail-closed）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 开发态占位值 | Django 密钥；`DEBUG=False` 时必填，生成：`python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `True` | 调试模式；`docker-compose.prod.yml` 已写死 `False` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1`（DEBUG 时） | 允许的主机名，逗号分隔；生产必填 |
| `DJANGO_TRUST_PROXY_HEADER` | `True` | 是否采信 `X-Forwarded-For` 取客户端 IP（登录限速用）；代理不受信时设 `False` |
| `ES_URL` | `http://127.0.0.1:9200` | Elasticsearch 地址（Django 与 Scrapy 统一使用；Docker 部署为 `http://elasticsearch:9200`） |
| `ES_INDEX` | `quotes` | 文档索引名（历史遗留命名，见 `crawler/pipelines.py`） |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | （空） | 管理员账号，建议写在 `XSearch/local_settings.py`（不入 git） |
| `API_TOKEN` | （空） | 非浏览器客户端的额外令牌；留空不启用。浏览器侧写接口不依赖它 |
| `AI_PROXIES` | （空） | 住宅代理列表（逗号分隔），按请求轮换 |
| `FRONTEND_ORIGINS` | （空） | 追加允许跨域的前端来源 |

## Docker 部署

```bash
cp .env.example .env        # 编辑 .env：DEBUG/ALLOWED_HOSTS/SECRET_KEY/管理员账号
docker compose up -d        # ES + Redis + 后端 + 前端

docker compose logs -f backend
docker compose exec backend scrapy crawl news_rss
```

本机访问：前端 http://localhost:8080 ｜ 后端 API http://localhost:8000 ｜ ES http://localhost:9200
（ES/Redis 仅绑定 127.0.0.1，`xpack.security` 关闭，切勿改回公网监听。）

生产拓扑（NAS）见 `docker-compose.prod.yml` 顶部注释：ES/Redis/后端不发布宿主端口，
公网唯一入口是前端 `127.0.0.1:5600` → frp → VPS Nginx → HTTPS。

## 测试

```bash
cd XSearch
python manage.py test search
```

覆盖：API 参数校验、CORS、管理员登录/登出/401、**登录限速与解锁**、**采集接口鉴权**、
**resume_job 路径穿越拦截**、ES 异常降级、数据管理读写、cron 校验、正文清洗、
**robots 合规与指纹伪装域名边界**、已移除模块不得回流。

前端暂无自动化测试，构建时由 `vue-tsc --noEmit` 做类型检查（`npm run build`）。

## 发布清单（公网部署前）

- [ ] `.env`：`DJANGO_DEBUG=False`、`DJANGO_ALLOWED_HOSTS=<你的域名>`、随机 `DJANGO_SECRET_KEY`
      （三项缺一后端拒起，这是有意的）
- [ ] 管理员口令改为随机长口令（不要用示例值；弱口令会在启动日志告警）
- [ ] Elasticsearch 仅内网：compose 内 `xpack.security.enabled=false`，切勿把 9200 暴露公网
- [ ] 依赖用 `requirements-lock.txt` 安装（Dockerfile 默认），保证构建可复现
- [ ] `python manage.py test search` 全绿后再发布
- [ ] `cd frontend && npm run build` 产物 `dist/` 与后端同批发布（前端为纯静态，无注入密钥）
- [ ] 部署后到「采集管理」页确认各定时任务"下次运行"时间正确（调度器随服务自启，无需访问页面激活）
- [ ] 采集合规：`ROBOTSTXT_OBEY=True`、默认发 `XSearchBot/1.0` UA；只有确需渲染的域名才进
      `crawler/settings.FINGERPRINT_HOSTS`，新增前先确认该站真的拒绝常规 UA

## 许可证

MIT，见 [LICENSE](LICENSE)。
