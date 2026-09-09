// 后端 API 封装：开发态走 Vite 代理（/api -> 127.0.0.1:8000）
// 生产部署可用 VITE_API_BASE 指向后端绝对地址
// 后端设置 API_TOKEN 时，前端构建时注入 VITE_API_TOKEN 即可自动携带
const BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN = import.meta.env.VITE_API_TOKEN || ''

function authHeaders(extra = {}) {
  return TOKEN ? { 'X-API-Token': TOKEN, ...extra } : extra
}

async function request(path, options = {}) {
  const headers = { ...authHeaders(), ...(options.headers || {}) }
  const resp = await fetch(BASE + path, { ...options, headers })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
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
