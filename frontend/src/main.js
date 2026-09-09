import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'

import SearchView from './views/SearchView.vue'
import NewsView from './views/NewsView.vue'
import RankingsView from './views/RankingsView.vue'
import StatsView from './views/StatsView.vue'
import CrawlView from './views/CrawlView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/search' },
    { path: '/search', component: SearchView },
    { path: '/news', component: NewsView },
    { path: '/rankings', component: RankingsView },
    { path: '/stats', component: StatsView },
    { path: '/crawl', component: CrawlView },
  ],
})

createApp(App).use(router).use(ElementPlus).mount('#app')
