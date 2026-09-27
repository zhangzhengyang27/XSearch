# XSearch 前端（Vue 3 + TypeScript + Vite + Element Plus）

调用后端（XSearch Django API）的独立前端工程，9 个视图组件 / 10 条路由：

| 路由 | 功能 | 调用接口 |
|---|---|---|
| `/` | 首页：检索入口 + 今日要闻（跨源混排、同题去重） | `/api/headlines/` |
| `/search` | 关键词搜索：补全建议、结果高亮、来源分面、排序、分页 | `/api/search/` `/api/suggest/` |
| `/news` | 新闻列表：中新网 / IT之家 / Solidot 分源分页 | `/api/rankings/` |
| `/ai` | AI 讯息：精选 / 日报 / 热点榜三 Tab | `/api/rankings/` |
| `/ai/detail` | AI 条目详情（按 `?id=` 精确取文，不再按标题猜） | `/api/doc/<id>/` |
| `/rankings` | 榜单：抖音热点榜 | `/api/rankings/` |
| `/stats` | 数据概览：总量、来源分布、热搜词 | `/api/stats/` |
| `/crawl` | 采集管理（需登录）：触发爬虫、状态与日志、定时任务 | `/api/crawl/*` |
| `/dbadmin` | 数据管理（需登录）：文档浏览/编辑/删除、按来源清理 | `/api/admin/db/*` |
| `/login` | 管理员登录 | `/api/auth/login/` |

`/crawl`、`/dbadmin` 由 `src/main.ts` 的路由守卫保护，未登录跳 `/login?next=…`；
清单定义在 `src/auth.ts` 的 `ADMIN_PATHS`（`api.ts` 判断 401 是否跳登录页时复用同一份）。
面向读者的页面上，「更新数据」等采集按钮仅 `isAdmin` 时渲染。

## 开发

```bash
npm install
npm run dev        # http://localhost:5173，已代理 /api 到 127.0.0.1:8000
```

后端先启动：`cd ../XSearch && python manage.py runserver 8000`

## 生产构建

```bash
npm run build      # 先跑 vue-tsc 类型检查，再产出静态资源到 dist/
npm run type-check # 仅类型检查（vue-tsc --noEmit）
npm run preview    # 本地预览构建产物（4173 端口，已在后端 CORS 白名单）
```

接口响应与文档条目的类型定义集中在 `src/api.ts`（DocItem / SearchResult 等），
`import.meta.env` 的 VITE_ 变量类型在 `src/vite-env.d.ts`。

跨域：开发态走 Vite 代理无需 CORS；直连部署时后端已开启白名单 CORS
（settings.CORS_ALLOW_ORIGINS / 环境变量 FRONTEND_ORIGINS）。
