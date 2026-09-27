// 后端 API 封装：开发态走 Vite 代理（/api -> 127.0.0.1:8000）
// 生产部署可用 VITE_API_BASE 指向后端绝对地址
// 后端设置 API_TOKEN 时，前端构建时注入 VITE_API_TOKEN 即可自动携带
import { adminToken, clearAuth, isAdminPath } from './auth'

// ---- 接口响应类型（字段与后端 search/api_views.py、crawl_manager.py 一一对应） ----

/** ES 文档条目：搜索 / 榜单 / 管理接口共用的字段子集，不同来源的字段可能缺省 */
export interface DocItem {
  url: string
  title: string
  content: string
  source: string
  author?: string
  rating?: number | null
  rank?: number | null
  view_nums?: number | null
  praise_nums?: number | null
  reply_nums?: number | null
  danmaku_nums?: number | null
  front_image_url?: string
  create_date?: string
  crawled_at?: string
  tags?: string[] | string
}

/** 管理接口返回的文档：列表与详情均带 ES _id */
export type DbDocRow = DocItem & { id: string }

export interface FacetBucket {
  key: string
  count: number
}

export interface Facets {
  sources: FacetBucket[]
  days: FacetBucket[]
}

export interface SearchResult {
  total: number
  page: number
  page_nums: number
  results: DocItem[]
  facets: Facets
  corrected: string | null
  fuzzy: boolean
  suggestions?: string[]
}

export interface RankingsResult {
  source: string
  total: number
  page?: number
  page_nums?: number
  items: DocItem[]
}

export interface AiItemResult {
  q: string
  total: number
  items: DocItem[]
}

export interface StatsResult {
  es_ok: boolean
  es_error?: string
  total: number
  by_source: FacetBucket[]
  top_keywords: string[]
}

export interface CrawlStatusResult {
  running: boolean
  /** 运行中为 running；空闲时为最近一次任务的结束状态（completed/failed/empty） */
  status: string | null
  started_at: string | null
  spider: string | null
  log_tail: string[]
}

export interface CrawlStartResult {
  started: boolean
  reason?: string
  label?: string
  pid?: number
  started_at?: string
}

export interface CrawlHistoryItem {
  job_id: string
  spider: string
  status: string
  started_at?: string
  ended_at?: string | null
  pid?: number | null
}

export interface CrawlStatsResult {
  total: number
  completed: number
  failed: number
  empty: number
  running: number
  success_rate: number
  by_day: { day: string; total: number; completed: number; failed: number; running: number }[]
  by_spider: { spider: string; total: number; completed: number; failed: number }[]
}

export interface SpiderInfo {
  key: string
  label: string
}

export interface ScheduleJob {
  id: string
  spider: string
  label: string
  cron: string
  pages: number
  js: boolean
  enabled: boolean
  created_at: string
  next_run?: string
}

/** 定时任务触发记录（最近 10 条） */
export interface ScheduleHistoryItem {
  job_id: string
  spider: string | null
  time: string
  status: string
  reason: string
}

export interface ScheduleListResult {
  jobs: ScheduleJob[]
  history: ScheduleHistoryItem[]
  scheduler_active: boolean
}

export interface ScheduleMutationResult {
  ok: boolean
  reason?: string
  job_id?: string
  scheduler_active?: boolean
}

export interface AdminLoginResult {
  ok: boolean
  token: string
  username: string
}

export interface DbOverviewResult {
  total: number
  size_bytes: number
  by_source: FacetBucket[]
}

export interface DbDocsResult {
  total: number
  page: number
  page_size: number
  page_nums: number
  items: DbDocRow[]
}

export interface DbDocUpdateFields {
  title?: string
  author?: string
  tags?: string[]
  content?: string
}

export interface DbPurgeResult {
  deleted: number
  ok?: boolean
  source?: string
}

interface RequestOptions extends RequestInit {
  headers?: Record<string, string>
}

const BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN = import.meta.env.VITE_API_TOKEN || ''

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = TOKEN ? { 'X-API-Token': TOKEN, ...extra } : { ...extra }
  if (adminToken.value) headers['X-Admin-Token'] = adminToken.value
  return headers
}

// 后端业务错误统一放 error 字段；非对象响应（如 suggest 的数组）取不到时用兜底文案
function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === 'object' && 'error' in data) {
    const err = (data as { error: unknown }).error
    if (typeof err === 'string') return err
  }
  return `请求失败 (${status})`
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = { ...authHeaders(), ...(options.headers || {}) }
  const resp = await fetch(BASE + path, { ...options, headers })
  const data: unknown = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    // 管理员登录态失效（排除登录接口自身的密码错误 401）：清除本地 token。
    // 只在管理页整页跳登录；公开页（/ai /news /rankings）就地报错——把匿名访客甩到
    // 他看不懂、也回不来的管理登录页是净损失（采集按钮在这些页已按 isAdmin 隐藏）。
    if (resp.status === 401 && !path.startsWith('/api/auth/login')) {
      clearAuth()
      if (isAdminPath(location.pathname)) {
        window.location.href = '/login?next=' +
          encodeURIComponent(location.pathname + location.search)
      }
    }
    throw new Error(errorMessage(data, resp.status))
  }
  return data as T
}

export const api = {
  search: (q: string, p = 1, source = '', sort = 'relevance', days = ''): Promise<SearchResult> => {
    let path = `/api/search/?q=${encodeURIComponent(q)}&p=${p}&sort=${encodeURIComponent(sort)}`
    if (source) path += `&source=${encodeURIComponent(source)}`
    if (days) path += `&days=${days}`
    return request(path)
  },
  suggest: async (s: string): Promise<string[]> => {
    if (!s.trim()) return []
    return request<string[]>(`/api/suggest/?s=${encodeURIComponent(s)}`)
  },
  stats: () => request<StatsResult>('/api/stats/'),
  aiItem: (q: string) => request<AiItemResult>(`/api/ai/item/?q=${encodeURIComponent(q)}`),
  crawlStart: (spider: string, pages: number, js: boolean) => request<CrawlStartResult>('/api/crawl/start/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spider, pages, js }),
  }),
  crawlStatus: () => request<CrawlStatusResult>('/api/crawl/status/'),
  crawlHistory: (limit = 50) => request<{ history: CrawlHistoryItem[] }>(`/api/crawl/history/?limit=${limit}`),
  crawlStats: () => request<CrawlStatsResult>('/api/crawl/stats/'),
  rankings: (source: string, p = 1) => request<RankingsResult>(`/api/rankings/?source=${encodeURIComponent(source)}&p=${p}`),
  crawlSpiders: () => request<{ spiders: SpiderInfo[] }>('/api/crawl/spiders/'),
  // 管理员登录（采集管理页鉴权）
  adminLogin: (username: string, password: string) => request<AdminLoginResult>('/api/auth/login/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }),
  adminLogout: () => request<{ ok: boolean }>('/api/auth/logout/', { method: 'POST' }),
  // ---- 数据管理（管理员，ES quotes 索引） ----
  dbOverview: () => request<DbOverviewResult>('/api/admin/db/overview/'),
  dbDocs: (source = '', q = '', p = 1): Promise<DbDocsResult> => {
    const sp = new URLSearchParams({ p: String(p) })
    if (source) sp.set('source', source)
    if (q) sp.set('q', q)
    return request(`/api/admin/db/docs/?${sp.toString()}`)
  },
  dbDoc: (id: string) => request<DbDocRow>(`/api/admin/db/doc/${encodeURIComponent(id)}/`),
  dbDocUpdate: (id: string, fields: DbDocUpdateFields) => request<{ ok: boolean }>(`/api/admin/db/doc/${encodeURIComponent(id)}/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  }),
  dbDocDelete: (id: string) => request<{ ok: boolean }>(`/api/admin/db/doc/${encodeURIComponent(id)}/`, {
    method: 'DELETE',
  }),
  dbPurge: (source: string) => request<DbPurgeResult>('/api/admin/db/purge/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, confirm: source }),
  }),
  // 定时任务
  scheduleList: () => request<ScheduleListResult>('/api/crawl/schedule/'),
  scheduleAdd: (spider: string, cron: string, pages = 2, js = false) =>
    request<ScheduleMutationResult>('/api/crawl/schedule/add/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spider, cron, pages, js }),
    }),
  scheduleRemove: (jobId: string) => request<{ ok: boolean }>('/api/crawl/schedule/remove/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId }),
  }),
  scheduleUpdate: (jobId: string, cron: string, spider: string) => request<ScheduleMutationResult>('/api/crawl/schedule/update/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, cron, spider }),
  }),
  scheduleToggle: (jobId: string, enabled: boolean | string | number) => request<ScheduleMutationResult>('/api/crawl/schedule/toggle/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, enabled }),
  }),
}

// ES 高亮片段含 HTML（<span class="kw">），渲染前整体转义、仅还原高亮标签，防 XSS
export function renderHighlight(html: string): string {
  if (!html) return ''
  const escaped = html
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  return escaped
    .replaceAll('&lt;span class="kw"&gt;', '<mark class="kw">')
    .replaceAll('&lt;/span&gt;', '</mark>')
    // ES 默认分词把中文拆成单字，合并相邻高亮为一个整词
    .replaceAll('</mark><mark class="kw">', '')
}

// 图床防盗链：图片统一走后端代理（带图床站内 Referer）
export function imgUrl(u: string): string {
  if (!u) return ''
  return `${BASE}/api/img/?u=${encodeURIComponent(u)}`
}

export function formatNum(n?: number | null): string {
  if (n == null) return ''
  return n >= 10000 ? (n / 10000).toFixed(1) + '万' : String(n)
}

// fetch 抛出的都是 Error，但 catch(e) 在 strict 模式下为 unknown，统一取文案
export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
