# AI 时代技术栈说明（2026-09 重写版）

老代码（Selenium/undetected-chromedriver、zheye/OpenCV 滑块、阿布云/西祠代理、
jobbole/拉勾/知乎老爬虫、fake-useragent）已全部移除，仓库只保留 2026 年的常用方案。

> **项目合并说明**：原 ArticleSpider 爬虫项目已合并入 LcvSearch，作为 `LcvSearch/crawler/` 子包。
> 所有爬虫命令均在 `LcvSearch/` 目录下执行，scrapy.cfg 已配置好。

## 一、技术栈与数据流

```
爬虫（Scrapy 2.13+，位于 LcvSearch/crawler/）
  ├─ news_rss           新闻 RSS 聚合（人民网/中新网/IT之家/Solidot，官方源合规抓取）
  ├─ aihot_news         AI 资讯+日报（AIHOT 官方 v1 API：精选动态带 LLM 摘要/评分/入选理由）
  ├─ aihot_hot          AI 热点榜（AIHOT 官方 v1 API，多信源印证事件 + AI 综述）
  ├─ douyin_hot         抖音热点榜 50 条（Playwright 渲染 + 页面文本解析）
  ├─ quotes_ai          演示站：选择器快路径 + DeepSeek 自愈兜底 + Playwright 渲染
  ├─ 公共设施             scrapy-playwright 渲染 / browserforge 整组指纹头 / 住宅代理轮换
  └─ 入库               EsArticlePipeline -> Elasticsearch "quotes" 索引
                            └─> /api/search/  关键词搜索 + 搜索建议
```

> 指纹头中间件用"替换"而非"追加"：Scrapy 内置 UA 中间件会先写默认 UA，
> setdefault 会被覆盖导致真实指纹头不生效（实测曾让目标站接口返回 412）。

## 二、AI 大模型：DeepSeek

统一走 OpenAI 兼容协议（`https://api.deepseek.com`），只需一个 key：

| 用途 | 模型（默认） | 说明 |
|---|---|---|
| 语义抽取兜底 | `deepseek-v4-flash` | 284B-MoE/13B 激活，快且便宜 |
| 滑块/点选验证码识别 | `deepseek-v4-flash-vision-exp` | 视觉版，图片按 token 计费 |

```bash
export AI_LLM_API_KEY="你的deepseek-key"
# 可选覆盖：AI_LLM_BASE_URL / AI_LLM_MODEL / AI_VLM_MODEL
```

不配置 key 时爬虫自动降级为纯选择器路线，不影响基础爬取。

## 三、安装与试跑

```bash
# 进入项目目录（爬虫 + 后端统一在这里）
cd LcvSearch
pip install -r requirements.txt
playwright install chromium   # 首次需要安装浏览器

# ===== 爬虫试跑 =====

# 演示站（quotes.toscrape.com）
scrapy crawl quotes_ai -a pages=2        # 1. 选择器快路径
scrapy crawl quotes_ai -a js=1           # 2. Playwright 渲染 JS 页
scrapy crawl quotes_ai --set SELECTORS_DISABLED=1   # 3. 模拟改版，看 LLM 自愈（需 key）

# 新闻 RSS 聚合（4 个官方源，配定时任务每天增量）
scrapy crawl news_rss                           # 全部源
scrapy crawl news_rss -a sources=news_people    # 指定源

# AI 资讯 + 日报（AIHOT 官方 API：精选动态带 LLM 摘要/评分，当日精编日报）
scrapy crawl aihot_news                         # 精选（7 天窗口）
scrapy crawl aihot_news -a full=1               # 全量动态（含未精选）

# AI 热点榜（AIHOT 官方 API，匿名只读，限速 2 秒间隔）
scrapy crawl aihot_hot

# 抖音热点榜（Playwright 渲染，约 30-60 秒）
scrapy crawl douyin_hot

# 知乎登录（VLM 识别滑块；目标站选择器可能随版本变动，按实测微调）
export ZHIHU_USER=... ZHIHU_PASS=... AI_LLM_API_KEY=...
python crawler/tools/zhihu_login_vlm.py

# ===== 后端搜索站 =====
python manage.py migrate                 # 首次运行初始化 sqlite
python manage.py runserver 127.0.0.1:8000
# API: http://127.0.0.1:8000/api/search/?q=...
```

## 四、代理配置（可选）

```bash
# 住宅/ISP 代理，标准认证，逗号分隔多个做轮换
export AI_PROXIES="http://user:pass@gate.provider.com:30001,http://user:pass@gate.provider.com:30002"
```

## 五、前后端分离（2026-09 已实施）

LcvSearch 已改造为**纯 JSON API 后端**，页面层由独立的 Vue 3 工程承担：

```
frontend/ (Vue3 + Vite + Element Plus, localhost:5173)
    /search   搜索（Tab切换/建议/分页）    ──┐
    /news     新闻列表（4源/时间倒序/分页） ─┤      LcvSearch API (Django, localhost:8000)
    /stats    数据概览（总量/来源/热搜）───┤      /api/search /api/suggest /api/stats
    /crawl    采集管理（触发+日志+定时任务）  ─┤      /api/crawl/* /api/rankings /api/img
    /rankings 榜单（AI 热点榜/抖音热点榜）   ─┘
后端以子进程方式运行 Scrapy 爬虫（CrawlManager），数据经 ES 管道回流到搜索。
```

技术栈依据（2026 选型调研，详见《前后端分离调研报告.md》）：Vue 3 为国内中小项目
快速落地首选；Vite 为官方工具链；Element Plus 仍是 Vue3 组件库默认选项。

```bash
# 终端 1：后端 API
cd LcvSearch && python manage.py migrate && python manage.py runserver 127.0.0.1:8000
# 终端 2：前端（开发态，Vite 已代理 /api 到 8000，免 CORS）
cd frontend && npm install && npm run dev     # http://localhost:5173
# 生产构建：npm run build（dist/ 纯静态可任意托管；npm run preview 本地预览）
```

## 六、前端体验升级（参照同类开源项目形态）

- **采集页对齐真实数据源**：可选 新闻RSS / AI热点榜 / 抖音热点 / 演示站
  （/api/crawl/spiders 白名单），不再与页面数据源脱节
- **榜单页多源聚合**：AI 热点榜（AIHOT 多信源印证）、抖音热点榜（实时50条）
- **新闻列表页**：4 源按发布时间倒序分页展示，可一键触发增量采集

## 七、法律提醒（比技术更硬的边界）

《数据安全法》《个人信息保护法》之后，司法实践中**绕过技术措施本身就是加重情节**，
登录后数据、个人信息是红线。爬取前先看目标站的 robots.txt 与 AI 爬虫政策
（Cloudflare 已支持默认封锁 AI 爬虫 + pay-per-crawl）。本仓库仅用于学习与已授权场景。
