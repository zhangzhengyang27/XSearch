// 后端 API 封装：开发态走 Vite 代理（/api -> 127.0.0.1:8000）
// 生产部署可用 VITE_API_BASE 指向后端绝对地址
// 后端设置 API_TOKEN 时，前端构建时注入 VITE_API_TOKEN 即可自动携带
import { adminToken, clearAuth } from './auth.js'

const BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN = import.meta.env.VITE_API_TOKEN || ''

function authHeaders(extra = {}) {
  const headers = TOKEN ? { 'X-API-Token': TOKEN, ...extra } : { ...extra }
  if (adminToken.value) headers['X-Admin-Token'] = adminToken.value
  return headers
}

async function request(path, options = {}) {
  const headers = { ...authHeaders(), ...(options.headers || {}) }
  const resp = await fetch(BASE + path, { ...options, headers })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    // 管理员登录态失效（排除登录接口自身的密码错误 401）：
    // 清除本地 token 并整页跳登录页，登录后回到当前地址
    if (resp.status === 401 && !path.startsWith('/api/auth/login')) {
      clearAuth()
      window.location.href = '/login?next=' +
        encodeURIComponent(location.pathname + location.search)
    }
    throw new Error(data.error || `请求失败 (${resp.status})`)
  }
  return data
}

export const api = {
  search: (q, p = 1, source = '', sort = 'relevance', days = '') => {
    let path = `/api/search/?q=${encodeURIComponent(q)}&p=${p}&sort=${encodeURIComponent(sort)}`
    if (source) path += `&source=${encodeURIComponent(source)}`
    if (days) path += `&days=${days}`
    return request(path)
  },
  suggest: async (s) => {
    if (!s.trim()) return []
    return request(`/api/suggest/?s=${encodeURIComponent(s)}`)
  },
  stats: () => request('/api/stats/'),
  aiItem: (q) => request(`/api/ai/item/?q=${encodeURIComponent(q)}`),
  crawlStart: (spider, pages, js) => request('/api/crawl/start/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spider, pages, js }),
  }),
  crawlStatus: () => request('/api/crawl/status/'),
  crawlHistory: (limit = 50) => request(`/api/crawl/history/?limit=${limit}`),
  crawlStats: () => request('/api/crawl/stats/'),
  rankings: (source, p = 1) => request(`/api/rankings/?source=${encodeURIComponent(source)}&p=${p}`),
  crawlSpiders: () => request('/api/crawl/spiders/'),
  // 管理员登录（采集管理页鉴权）
  adminLogin: (username, password) => request('/api/auth/login/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }),
  adminLogout: () => request('/api/auth/logout/', { method: 'POST' }),
  // ---- 数据管理（管理员，ES quotes 索引） ----
  dbOverview: () => request('/api/admin/db/overview/'),
  dbDocs: (source = '', q = '', p = 1) => {
    const sp = new URLSearchParams({ p: String(p) })
    if (source) sp.set('source', source)
    if (q) sp.set('q', q)
    return request(`/api/admin/db/docs/?${sp.toString()}`)
  },
  dbDoc: (id) => request(`/api/admin/db/doc/${encodeURIComponent(id)}/`),
  dbDocUpdate: (id, fields) => request(`/api/admin/db/doc/${encodeURIComponent(id)}/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  }),
  dbDocDelete: (id) => request(`/api/admin/db/doc/${encodeURIComponent(id)}/`, {
    method: 'DELETE',
  }),
  dbPurge: (source) => request('/api/admin/db/purge/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, confirm: source }),
  }),
  // 定时任务
  scheduleList: () => request('/api/crawl/schedule/'),
  scheduleAdd: (spider, cron, pages = 2, js = false) =>
    request('/api/crawl/schedule/add/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spider, cron, pages, js }),
    }),
  scheduleRemove: (jobId) => request('/api/crawl/schedule/remove/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId }),
  }),
  scheduleUpdate: (jobId, cron, spider) => request('/api/crawl/schedule/update/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, cron, spider }),
  }),
  scheduleToggle: (jobId, enabled) => request('/api/crawl/schedule/toggle/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, enabled }),
  }),
}

// ES 高亮片段含 HTML（<span class="kw">），渲染前整体转义、仅还原高亮标签，防 XSS
export function renderHighlight(html) {
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
export function imgUrl(u) {
  if (!u) return ''
  return `${BASE}/api/img/?u=${encodeURIComponent(u)}`
}

export function formatNum(n) {
  if (n == null) return ''
  return n >= 10000 ? (n / 10000).toFixed(1) + '万' : String(n)
}
