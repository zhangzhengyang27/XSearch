import { createApp } from 'vue'
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'
import { isAdmin, ADMIN_PATHS } from './auth'

// 页面组件全部懒加载：此前 9 个视图静态 import 打成单个 1.1MB chunk，
// 读者只想搜个词也得先把「数据管理」表格的代码下完
const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/ai' },
  { path: '/search', component: () => import('./views/SearchView.vue'),
    meta: { title: '搜索', desc: '按关键词检索已采集的新闻与 AI 资讯，支持来源、时间与排序筛选' } },
  { path: '/news', component: () => import('./views/NewsView.vue'),
    meta: { title: '新闻', desc: '中新网、IT之家、Solidot 三源新闻按发布时间倒序排列' } },
  { path: '/ai', component: () => import('./views/AiView.vue'),
    meta: { title: 'AI 讯息', desc: 'AI 精选动态、AI 日报与 AI 热点榜（多信源印证事件）' } },
  { path: '/ai/detail', component: () => import('./views/AiDetailView.vue'),
    meta: { title: '条目详情', desc: 'AI 摘要与出处原文入口' } },
  { path: '/rankings', component: () => import('./views/RankingsView.vue'),
    meta: { title: '榜单', desc: '实时热点榜单' } },
  { path: '/stats', component: () => import('./views/StatsView.vue'),
    meta: { title: '数据概览', desc: '索引文档数、来源分布与热搜词' } },
  { path: '/crawl', component: () => import('./views/CrawlView.vue'),
    meta: { title: '采集管理', noIndex: true } },
  { path: '/dbadmin', component: () => import('./views/DbAdminView.vue'),
    meta: { title: '数据管理', noIndex: true } },
  { path: '/login', component: () => import('./views/LoginView.vue'),
    meta: { title: '管理员登录', noIndex: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

const SITE = 'XSearch · AI 搜索'

function ensureMeta(name: string): HTMLMetaElement {
  let tag = document.head.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)
  if (!tag) {
    tag = document.createElement('meta')
    tag.setAttribute('name', name)
    document.head.appendChild(tag)
  }
  return tag
}

// 管理页加 noindex：后台不该出现在搜索结果里
function setMeta(title: string, desc: string, noIndex: boolean): void {
  document.title = title ? `${title} · ${SITE}` : SITE
  if (desc) ensureMeta('description').setAttribute('content', desc)
  if (noIndex) {
    ensureMeta('robots').setAttribute('content', 'noindex, nofollow')
  } else {
    document.head.querySelector('meta[name="robots"]')?.remove()
  }
}

router.afterEach((to) => {
  const meta = to.meta as { title?: string; desc?: string; noIndex?: boolean }
  setMeta(meta.title || '', meta.desc || '', !!meta.noIndex)
})

// 管理页仅管理员可用：未登录跳登录页（登录后回到原地址）；已登录访问 /login 直接进采集管理
router.beforeEach((to) => {
  if (ADMIN_PATHS.includes(to.path) && !isAdmin.value) {
    return { path: '/login', query: { next: to.fullPath } }
  }
  if (to.path === '/login' && isAdmin.value) {
    return { path: '/crawl' }
  }
})

createApp(App).use(router).use(ElementPlus).mount('#app')
