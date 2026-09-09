<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-button @click="load" :loading="loading">刷新</el-button>
      <el-tag v-if="data.es_ok" type="success" effect="plain">Elasticsearch 正常</el-tag>
      <el-tag v-else type="danger" effect="plain">Elasticsearch 不可用</el-tag>
    </div>

    <el-row :gutter="16" class="cards">
      <el-col :span="8">
        <el-card shadow="never"><el-statistic title="库内文档总数" :value="data.total" /></el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never"><el-statistic title="数据来源" :value="data.by_source.length" suffix="类" /></el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never"><el-statistic title="热搜词" :value="data.top_keywords.length" suffix="个" /></el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="block" v-if="data.by_source.length">
      <template #header><b>来源分布</b></template>
      <div v-for="s in data.by_source" :key="s.key" class="row">
        <span class="label">{{ sourceName(s.key) }}</span>
        <el-progress :percentage="Math.round(s.count / Math.max(data.total, 1) * 100)" />
        <span class="count">{{ s.count }}</span>
      </div>
    </el-card>

    <el-card shadow="never" class="block" v-if="data.top_keywords.length">
      <template #header><b>热门搜索词（Redis 统计）</b></template>
      <el-tag v-for="k in data.top_keywords" :key="k" class="kw-tag" effect="plain">{{ k }}</el-tag>
    </el-card>

    <el-alert v-if="data.es_error" :title="data.es_error" type="warning" :closable="false" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const data = ref({ es_ok: true, total: 0, by_source: [], top_keywords: [] })
const loading = ref(false)

const SOURCE_NAMES = {
  bilibili_hot: 'B站热门', bilibili_weekly: 'B站每周必看', bilibili_comments: 'B站评论',
  douban_movie: '豆瓣电影', douban_book: '豆瓣图书',
  bilibili_video: 'B站搜索', netease_music: '网易云音乐', juejin_article: '掘金文章',
  news_people: '人民网', news_chinanews: '中新网',
  news_ithome: 'IT之家', news_solidot: 'Solidot',
  quotes_ai: '演示站',
}
const sourceName = (s) => SOURCE_NAMES[s] || s

async function load() {
  loading.value = true
  try { data.value = await api.stats() } finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
.cards { margin-bottom: 16px; }
.block { margin-bottom: 16px; }
.row { display: flex; align-items: center; gap: 12px; padding: 4px 0; }
.row .label { width: 110px; font-size: 13px; }
.row :deep(.el-progress) { flex: 1; }
.row .count { width: 60px; text-align: right; font-size: 13px; color: var(--el-text-color-secondary); }
.kw-tag { margin: 0 8px 8px 0; }
</style>
