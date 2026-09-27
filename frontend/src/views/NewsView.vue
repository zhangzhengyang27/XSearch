<template>
  <div>
    <div class="toolbar">
      <el-tabs v-model="tab" @tab-change="() => load(1)">
        <el-tab-pane label="全部新闻" name="news" />
        <el-tab-pane label="中新网" name="news_chinanews" />
        <el-tab-pane label="IT之家" name="news_ithome" />
        <el-tab-pane label="Solidot" name="news_solidot" />
      </el-tabs>
      <el-button size="small" @click="load(page)" :loading="loading">刷新</el-button>
      <!-- 采集会拉起子进程，仅管理员可见（后端 /api/crawl/* 同样要求 X-Admin-Token） -->
      <el-button v-if="isAdmin" size="small" type="primary" @click="recrawl" :loading="starting"
                 :disabled="crawl.running">
        {{ crawl.running ? '采集中…' : '采集最新新闻' }}
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />
    <el-empty v-if="!loading && !items.length"
              :description="isAdmin ? '暂无新闻，点右上角「采集最新新闻」抓取' : '暂无新闻'" />

    <article v-for="it in items" :key="it.url" class="news-card">
      <div class="body">
        <a :href="it.url" target="_blank" rel="noopener" class="title">{{ it.title }}</a>
        <div class="meta">
          <el-tag size="small" effect="plain" type="primary">{{ sourceName(it.source) }}</el-tag>
          <span v-if="it.rating != null" class="score">⭐ AI评分 {{ it.rating }}</span>
          <span v-if="it.create_date" class="dim">📅 {{ it.create_date }}</span>
        </div>
        <p class="desc" v-if="it.content">{{ it.content }}</p>
      </div>
    </article>

    <div class="pager" v-if="pageNums > 1">
      <el-pagination layout="prev, pager, next" :total="total" :page-size="20"
                     :current-page="page" @current-change="load" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errText, type DocItem, type RankingsResult } from '../api'
import { isAdmin } from '../auth'

const tab = ref('news')
const items = ref<DocItem[]>([])
const total = ref(0)
const page = ref(1)
const pageNums = ref(0)
const loading = ref(false)
const error = ref('')
const starting = ref(false)
const crawl = ref<{ running: boolean }>({ running: false })
let pollTimer: ReturnType<typeof setTimeout> | undefined

const SOURCE_NAMES: Record<string, string> = {
  news_people: '人民网', news_chinanews: '中新网',
  news_ithome: 'IT之家', news_solidot: 'Solidot',
}
const sourceName = (s: string): string => SOURCE_NAMES[s] || s

async function load(p = 1): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const d: RankingsResult = await api.rankings(tab.value, p)
    items.value = d.items
    total.value = d.total
    page.value = d.page || p
    pageNums.value = d.page_nums || 0
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

async function recrawl(): Promise<void> {
  starting.value = true
  try {
    const r = await api.crawlStart('news_rss', 1, false)
    if (r.started) {
      ElMessage.success('新闻采集已启动，完成后点「刷新」查看')
      crawl.value.running = true
      poll()
    } else {
      ElMessage.warning(r.reason || '启动失败')
    }
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    starting.value = false
  }
}

async function poll(): Promise<void> {
  try {
    const s = await api.crawlStatus()
    crawl.value.running = s.running
    if (!s.running) {
      ElMessage.success(s.status === 'empty'
        ? '采集结束但没有抓到数据，可能被目标站拦截'
        : '采集完成，点「刷新」查看新数据')
      return
    }
  } catch { /* 忽略轮询失败 */ }
  pollTimer = setTimeout(poll, 3000)
}

onMounted(() => load(1))
onUnmounted(() => clearTimeout(pollTimer))
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.toolbar :deep(.el-tabs) { flex: 1; min-width: 0; }
/* 移动端：标签行独占一行，按钮换到下一行，避免被 tabs 挤出视口 */
@media (max-width: 768px) {
  .toolbar { flex-wrap: wrap; gap: 8px; }
  .toolbar :deep(.el-tabs) { flex: 1 1 100%; }
  .toolbar :deep(.el-tabs__item) { padding: 0 12px; font-size: 14px; }
}
.toolbar :deep(.el-tabs__header) { margin-bottom: 0; }
.block { margin-bottom: 12px; }
.news-card {
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 12px 16px; margin-bottom: 10px;
  transition: box-shadow .2s;
}
.news-card:hover { box-shadow: var(--el-box-shadow-light); }
.title { font-size: 15px; font-weight: 600; color: var(--el-color-primary); text-decoration: none; }
.title:hover { text-decoration: underline; }
.meta { display: flex; gap: 12px; align-items: center; margin: 6px 0; }
.dim { font-size: 12px; color: var(--el-text-color-secondary); }
.score { font-size: 12px; color: #f7ba2a; }
.desc { font-size: 13px; color: var(--el-text-color-regular); margin: 0; line-height: 1.7; }
.pager { display: flex; justify-content: center; margin-top: 20px; }
</style>
