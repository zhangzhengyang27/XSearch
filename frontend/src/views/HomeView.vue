<template>
  <div class="home">
    <section class="hero">
      <h1 class="h1">XSearch</h1>
      <p class="sub">跨源中文新闻与 AI 资讯 · 全文检索</p>
      <el-input v-model="q" size="large" placeholder="搜新闻 / 文章" clearable
                @keyup.enter="go">
        <template #append>
          <el-button type="primary" @click="go">搜索</el-button>
        </template>
      </el-input>
      <div class="hot" v-if="hotWords.length">
        <span class="dim">大家都在搜</span>
        <el-tag v-for="w in hotWords" :key="w" class="hot-tag" effect="plain"
                @click="pick(w)">{{ w }}</el-tag>
      </div>
    </section>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

    <section class="today">
      <div class="sec-head">
        <h2 class="h2">今日要闻</h2>
        <span class="dim" v-if="items.length">最近 {{ hours }} 小时 · 同题已去重</span>
      </div>
      <el-skeleton v-if="loading" :rows="5" animated />
      <el-empty v-else-if="!items.length && !error"
                description="暂时没有新内容。采集任务在每天 8 点前后跑，稍后再来看。" />

      <article v-for="it in items" :key="it.id || it.url" class="card">
        <!-- AI 内容有站内详情页；新闻条目站内没有正文页，直接回源站 -->
        <router-link v-if="isAi(it)" :to="{ path: '/ai/detail', query: { id: it.id } }"
                     class="title">{{ it.title }}</router-link>
        <a v-else :href="it.url" target="_blank" rel="noopener" class="title">{{ it.title }}</a>
        <div class="meta">
          <el-tag size="small" effect="plain" type="primary">{{ sourceName(it.source) }}</el-tag>
          <el-tag v-if="isAi(it)" class="ai-tag" size="small" type="info" effect="plain">AI 摘要</el-tag>
          <span class="dim" v-if="it.create_date">📅 {{ it.create_date }}</span>
        </div>
        <p class="desc" v-if="it.content">{{ it.content }}</p>
      </article>
    </section>

    <section class="boards">
      <router-link v-for="b in BOARDS" :key="b.path" :to="b.path" class="board">
        <span class="board-name">{{ b.name }}</span>
        <span class="dim">{{ b.desc }}</span>
      </router-link>
    </section>
  </div>
</template>

<script setup lang="ts">
// 首页 = 检索入口 + 今日要闻混排。此前 / 直接重定向到 /ai（上游站点的镜像），
// 对第一次来的读者既没有"这里是干什么的"也没有可看的全貌。
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, errText, type DocItem } from '../api'

const router = useRouter()
const q = ref('')
const items = ref<DocItem[]>([])
const hotWords = ref<string[]>([])
const hours = ref(36)
const loading = ref(true)
const error = ref('')

const AI_SOURCES = ['aihot_news', 'aihot_daily', 'aihot_hot']
const isAi = (it: DocItem): boolean => AI_SOURCES.includes(it.source)

const SOURCE_NAMES: Record<string, string> = {
  news_chinanews: '中新网', news_ithome: 'IT之家', news_solidot: 'Solidot',
  aihot_news: 'AI 精选', aihot_daily: 'AI 日报', aihot_hot: 'AI 热点榜',
}
const sourceName = (s: string): string => SOURCE_NAMES[s] || s

const BOARDS = [
  { path: '/ai', name: 'AI 讯息', desc: '精选、日报与热点榜' },
  { path: '/news', name: '新闻', desc: '中新网 / IT之家 / Solidot' },
  { path: '/rankings', name: '榜单', desc: '实时热点排行' },
  { path: '/search', name: '检索', desc: '按来源、时间、排序筛选' },
]

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const d = await api.headlines()
    items.value = d.items
    hotWords.value = d.top_keywords
    hours.value = d.hours
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

function go(): void {
  const term = q.value.trim()
  if (!term) return
  router.push({ path: '/search', query: { q: term } })
}

function pick(w: string): void {
  q.value = w
  go()
}

onMounted(load)
</script>

<style scoped>
.hero { margin-bottom: 24px; }
.h1 { font-size: 30px; margin: 0 0 4px; letter-spacing: .5px; }
.sub { margin: 0 0 14px; color: var(--el-text-color-secondary); font-size: 13.5px; }
.hot { margin-top: 10px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hot-tag { cursor: pointer; }
.block { margin-bottom: 16px; }
.sec-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.h2 { font-size: 17px; margin: 0; }
.card {
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 12px 16px; margin-bottom: 10px;
  transition: box-shadow .2s;
}
.card:hover { box-shadow: var(--el-box-shadow-light); }
.title { font-size: 15px; font-weight: 600; color: var(--el-color-primary); text-decoration: none; }
.title:hover { text-decoration: underline; }
.meta { display: flex; gap: 10px; align-items: center; margin: 6px 0; flex-wrap: wrap; }
.dim { font-size: 12px; color: var(--el-text-color-secondary); }
.desc { font-size: 13px; color: var(--el-text-color-regular); margin: 0; line-height: 1.7; }
.boards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 24px; }
.board {
  display: flex; flex-direction: column; gap: 4px; text-decoration: none;
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 12px 14px;
}
.board:hover { border-color: var(--el-color-primary); }
.board-name { font-size: 14px; font-weight: 600; color: var(--el-color-primary); }

@media (max-width: 768px) {
  .h1 { font-size: 24px; }
  .boards { grid-template-columns: repeat(2, 1fr); }
}
</style>
