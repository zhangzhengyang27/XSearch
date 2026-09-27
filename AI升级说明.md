# AI 时代技术栈说明（2026-09 重写版）

老代码（Selenium/undetected-chromedriver、zheye/OpenCV 模板匹配滑块、阿布云/西祠代理、
jobbole/拉勾/知乎老爬虫、fake-useragent）已全部移除，仓库只保留 2026 年的常用方案。

> **项目合并说明**：原 ArticleSpider 爬虫项目已合并入 XSearch，作为 `XSearch/crawler/` 子包。
> 所有爬虫命令均在 `XSearch/` 目录下执行，scrapy.cfg 已配置好。

## 一、技术栈与数据流

```
爬虫（Scrapy 2.18，位于 XSearch/crawler/）
  ├─ news_rss           新闻 RSS 聚合（中新网/IT之家/Solidot，官方源合规抓取）
  ├─ news_backfill      中新网历史存档回填（180 天，幂等）
  ├─ aihot_news         AI 资讯+日报（AIHOT 官方 v1 API：精选动态带第三方 LLM 摘要/评分/入选理由）
  ├─ aihot_hot          AI 热点榜（AIHOT 官方 v1 API，多信源印证事件 + AI 综述）
  ├─ douyin_hot         抖音热点榜（Playwright 渲染 + 页面文本解析）
  ├─ 公共设施            scrapy-playwright 渲染 / browserforge 指纹（仅必要域名）/ 住宅代理轮换
  └─ 入库               EsArticlePipeline -> Elasticsearch "quotes" 索引
                            └─> /api/search/  关键词搜索 + 搜索建议
```

采集侧默认 **遵守 robots.txt**（`crawler/settings.py: ROBOTSTXT_OBEY = True`），
并自报身份 `XSearchBot/1.0`。2026-09-27 逐源核过 robots：RSS 各源与 aihot.news
（`Allow: /api/v1/`）均放行，抖音 robots 没有 `User-agent: *` 禁项。

> **指纹头中间件用"替换"而非"追加"**：内置 UserAgentMiddleware（优先级 500）会先写入
> `USER_AGENT`，`setdefault` 不会覆盖它，导致真实指纹头不生效（实测曾让目标站接口返回 412）。
> 现在这套头只对 `FINGERPRINT_HOSTS` 列出的域名生效（目前只有抖音：诚实 UA 请求
> `www.douyin.com/hot` 返回 444，nginx 层主动拒绝），其余请求一律走自报身份的 UA。

## 二、LLM 能力：现状是「没有」

本项目**不再自带任何 LLM 调用**：

- 语义抽取兜底（`crawler/ai/llm_extract.py`）：选择器路线已覆盖现有全部源，它没有调用方；
- 视觉模型识别滑块/点选验证码（`crawler/ai/vlm_captcha.py`）与知乎模拟登录
  （`crawler/tools/zhihu_login_vlm.py`）：属于绕过目标站技术措施，见第七节；
- 统一客户端 `common/llm_client.py`：只被上面两者引用，一并移除。

三者于 2026-09 删除（git 历史可回溯）。AI 内容里的摘要/评分是上游 AIHOT 已经算好的字段，
本项目只搬运、不自己调模型，因此 `AI_LLM_API_KEY` 等环境变量也随之取消。

要恢复"抽取兜底"能力，前提是：先有真实调用方，再确认目标站条款允许。

## 三、安装与试跑

```bash
# 进入项目目录（爬虫 + 后端统一在这里）
cd XSearch
pip install -r requirements-lock.txt
playwright install chromium   # 仅采集抖音榜需要

# ===== 爬虫试跑 =====

# 新闻 RSS 聚合（3 个官方源，配定时任务每天增量）
scrapy crawl news_rss
scrapy crawl news_rss -a sources=news_chinanews    # 指定源

# 中新网历史回填（幂等，可重复跑；用于把语料从"7 天"扩到"半年"）
scrapy crawl news_backfill -a days=180

# AI 资讯 + 日报（AIHOT 官方 API：精选动态带 LLM 摘要/评分，当日精编日报）
scrapy crawl aihot_news
scrapy crawl aihot_news -a full=1               # 全量动态（含未精选）

# AI 热点榜（AIHOT 官方 API，匿名只读，限速 2 秒间隔）
scrapy crawl aihot_hot

# 抖音热点榜（Playwright 渲染，约 30-60 秒）
scrapy crawl douyin_hot

# ===== 后端搜索站 =====
python manage.py migrate                 # 首次运行初始化 sqlite
python manage.py runserver 127.0.0.1:8000
# API: http://127.0.0.1:8000/api/search/?q=...
```

## 四、代理配置（可选）

默认空 = 直连。只有当某台目标站按 IP 限速时才需要，配置后按请求轮换：

```bash
export AI_PROXIES="http://user:pass@gate.provider.com:30001,http://user:pass@gate.provider.com:30002"
```

## 五、前后端分离（2026-09 已实施）

XSearch 已改造为**纯 JSON API 后端**，页面层由独立的 Vue 3 工程承担：

```
frontend/ (Vue3 + Vite + Element Plus, localhost:5173)
    /search   搜索（补全/分面/排序/分页）    ──┐
    /news     新闻列表（3源/时间倒序/分页）   ─┤
    /ai       AI（精选/日报/热点榜 三 Tab）  ─┤   XSearch API (Django, localhost:8000)
    /rankings 榜单（抖音热点榜）              ─┤   /api/search /api/suggest /api/stats
    /stats    数据概览                        ─┤   /api/rankings /api/crawl/* /api/img
    /crawl /dbadmin /login  采集与数据管理（管理员登录）──┘
后端以子进程方式运行 Scrapy 爬虫（CrawlManager），数据经 ES 管道回流到搜索。
```

技术栈依据：Vue 3 为国内中小项目快速落地首选；Vite 为官方工具链；Element Plus
仍是 Vue3 组件库默认选项。（选型调研笔记为内部文档，不入公开仓库。）

## 六、前端形态说明

- **采集页与真实数据源对齐**：可选 新闻RSS / AI资讯日报 / AI热点榜 / 抖音热点
  （`/api/crawl/spiders` 白名单），不与页面数据源脱节
- **榜单页**：AI 热点榜（AIHOT 多信源印证）、抖音热点榜（实时 50 条）
- **新闻列表页**：3 源按发布时间倒序分页展示，管理员可一键触发增量采集
- **采集类按钮对匿名访客隐藏**：`/api/crawl/*` 已要求管理员登录，按钮按 `isAdmin` 渲染，
  避免读者点了被弹到管理登录页

## 七、法律与合规提醒（比技术更硬的边界）

《数据安全法》《个人信息保护法》之后，司法实践中**绕过技术措施本身就是加重情节**，
登录后数据、个人信息是红线。爬取前先看目标站的 robots.txt 与爬虫政策
（Cloudflare 已支持默认封锁 AI 爬虫 + pay-per-crawl）。

本仓库当前的立场：默认遵守 robots、自报 UA、限速礼貌（`DOWNLOAD_DELAY=1` + AUTOTHROTTLE）、
不采集登录后数据、不含验证码识别工具。仅用于学习与已获授权的抓取场景。

**已知的例外与张力（诚实记录）**：`douyin_hot` 保留在仓库中，而该站对常规 UA 返回 444，
取数依赖 Playwright + 浏览器指纹伪装 —— 这与上面第一句是冲突的。已做的收敛是把伪装限制在
`FINGERPRINT_HOSTS` 白名单内（不再全局生效）。若这个仓库要继续对外公开，
建议评估：下掉该源，或改用不依赖伪装的公开接口。
