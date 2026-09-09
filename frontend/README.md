# XSearch 前端（Vue 3 + Vite + Element Plus）

调用后端（XSearch Django API）的独立前端工程，四个视图：

| 路由 | 功能 | 调用接口 |
|---|---|---|
| `/search` | 关键词搜索：补全建议、结果高亮、分页 | `/api/search/` `/api/suggest/` |
| `/rankings` | 榜单：抖音热点 / B站热门 / 豆瓣 Top250 | `/api/rankings/` |
| `/stats` | 数据概览：总量、来源分布、热搜词 | `/api/stats/` |
| `/crawl` | 采集管理：触发爬虫、状态与日志轮询 | `/api/crawl/start/` `/api/crawl/status/` |

## 开发

```bash
npm install
npm run dev        # http://localhost:5173，已代理 /api 到 127.0.0.1:8000
```

后端先启动：`cd ../XSearch && python manage.py runserver 8000`

## 生产构建

```bash
npm run build      # 产物在 dist/，任意静态服务器可托管
npm run preview    # 本地预览构建产物（4173 端口，已在后端 CORS 白名单）
```

跨域：开发态走 Vite 代理无需 CORS；直连部署时后端已开启白名单 CORS
（settings.CORS_ALLOW_ORIGINS / 环境变量 FRONTEND_ORIGINS）。
