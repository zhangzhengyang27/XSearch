import { createApp } from 'vue'
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'

import SearchView from './views/SearchView.vue'
import NewsView from './views/NewsView.vue'
import AiView from './views/AiView.vue'
import AiDetailView from './views/AiDetailView.vue'
import RankingsView from './views/RankingsView.vue'
import StatsView from './views/StatsView.vue'
import CrawlView from './views/CrawlView.vue'
import DbAdminView from './views/DbAdminView.vue'
import LoginView from './views/LoginView.vue'
import { isAdmin, ADMIN_PATHS } from './auth'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/ai' },
  { path: '/search', component: SearchView },
  { path: '/news', component: NewsView },
  { path: '/ai', component: AiView },
  { path: '/ai/detail', component: AiDetailView },
  { path: '/rankings', component: RankingsView },
  { path: '/stats', component: StatsView },
  { path: '/crawl', component: CrawlView },
  { path: '/dbadmin', component: DbAdminView },
  { path: '/login', component: LoginView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
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
