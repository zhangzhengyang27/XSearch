<template>
  <div>
    <div class="toolbar">
      <el-tabs v-model="tab" @tab-change="() => load(1)">
        <el-tab-pane label="AI 精选" name="selected" />
        <el-tab-pane label="AI 日报" name="daily" />
        <el-tab-pane label="AI 热点榜" name="hot" />
      </el-tabs>
      <el-button size="small" @click="load(page)" :loading="loading">刷新</el-button>
      <el-button size="small" type="primary" @click="recrawl" :loading="starting"
                 :disabled="crawl.running">
        {{ crawl.running ? '采集中…' : '更新数据' }}
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />
    <el-alert v-if="hint" :title="hint" type="info" show-icon :closable="false" class="block" />
    <el-empty v-if="!loading && !items.length"
              description="暂无数据，点右上角「更新数据」采集" />

    <!-- AI 精选：feed 流（LLM 摘要 + 评分） -->
    <template v-if="tab === 'selected'">
      <article v-for="it in items" :key="it.url" class="news-card">
        <a :href="it.url" target="_blank" rel="noopener" class="title">{{ it.title }}</a>
        <div class="meta">
          <el-tag v-if="it.rating != null" size="small" effect="dark" type="warning"
                  class="score-tag">AI 评分 {{ it.rating }}</el-tag>
          <span class="dim">📡 {{ it.author }}</span>
          <span v-if="it.create_date" class="dim">📅 {{ it.create_date }}</span>
        </div>
        <p class="desc" v-if="it.content">{{ it.content }}</p>
      </article>
      <div class="pager" v-if="pageNums > 1">
        <el-pagination layout="prev, pager, next" :total="total" :page-size="20"
                       :current-page="page" @current-change="load" />
      </div>
    </template>

    <!-- AI 热点榜：排名卡（多信源印证 + AI 综述） -->
    <template v-else-if="tab === 'hot'">
      <article v-for="it in items" :key="it.url" class="rank-card">
        <div class="rank-no" :class="{ top: it.rank <= 3 }">{{ it.rank }}</div>
        <div class="body">
          <a :href="it.url" target="_blank" rel="noopener" class="title">{{ it.title }}</a>
          <div class="meta">
            <span v-if="it.view_nums != null">🔗 {{ it.view_nums }} 信源印证</span>
            <span v-if="it.create_date" class="dim">📅 {{ it.create_date }}</span>
          </div>
          <p class="desc" v-if="it.content">{{ it.content }}</p>
        </div>
      </article>
    </template>

    <!-- AI 日报：折叠面板，按日期展开全文 -->
    <template v-else>
      <el-collapse accordion v-model="openDaily" class="daily-list">
        <el-collapse-item v-for="it in items" :key="it.url" :name="it.create_date || it.title">
          <template #title>
            <div class="daily-title">
              <b>{{ it.title }}</b>
              <span class="dim" v-if="it.create_date">{{ it.create_date }}</span>
            </div>
          </template>
          <div class="daily-body">{{ it.content }}</div>
          <div class="daily-link">
            <a :href="it.url" target="_blank" rel="noopener">在 AIHOT 查看原文页 →</a>
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api.js'

const tab = ref('selected')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageNums = ref(0)
const loading = ref(false)
const error = ref('')
const hint = ref('')
const starting = ref(false)
const crawl = ref({ running: false })
const openDaily = ref('')
let pollTimer = null

// 各 Tab 对应的 ES source 与提示文案（信息架构参照 aihot.news：
// 精选 feed / 日报阅读 / 热点榜排行 三种内容类型独立呈现）
const TAB_SOURCE = { selected: 'aihot_news', daily: 'aihot_daily', hot: 'aihot_hot' }
const HINTS = {
  selected: 'AIHOT 自动聚合数百个信源，由 LLM 摘要并打分精选（aihot.news 官方 API）',
  daily: 'AIHOT 每天 8:00（北京时间）发布精编日报，点击日期展开全文',
  hot: '过去 48 小时内被多个独立信源共同印证的 AI 事件',
}

async function load(p = 1) {
  loading.value = true
  error.value = ''
  hint.value = HINTS[tab.value] || ''
  try {
    const d = await api.rankings(TAB_SOURCE[tab.value], p)
    items.value = d.items
    total.value = d.total
    page.value = d.page || p
    pageNums.value = d.page_nums || 0
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onTab() {
  items.value = []
  load(1)
}

async function recrawl() {
  starting.value = true
  // 热点榜走 aihot_hot 爬虫；精选与日报同属 aihot_news 爬虫
  const spider = tab.value === 'hot' ? 'aihot_hot' : 'aihot_news'
  try {
    const r = await api.crawlStart(spider, 1, false)
    if (r.started) {
      ElMessage.success('采集任务已启动，完成后点「刷新」查看')
      crawl.value.running = true
      poll()
    } else {
      ElMessage.warning(r.reason || '启动失败')
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    starting.value = false
  }
}

async function poll() {
  try {
    const s = await api.crawlStatus()
    crawl.value.running = s.running
    if (!s.running) {
      ElMessage.success(s.status === 'empty'
        ? '采集结束但没有抓到数据，可能被目标站限流'
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
.toolbar :deep(.el-tabs) { flex: 1; }
.toolbar :deep(.el-tabs__header) { margin-bottom: 0; }
.block { margin-bottom: 12px; }

.news-card, .rank-card {
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 12px 16px; margin-bottom: 10px;
  transition: box-shadow .2s;
}
.news-card:hover, .rank-card:hover { box-shadow: var(--el-box-shadow-light); }
.rank-card { display: flex; gap: 14px; align-items: flex-start; }
.rank-no {
  width: 34px; height: 34px; border-radius: 8px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; background: var(--el-fill-color); color: var(--el-text-color-secondary);
}
.rank-no.top { background: #f7ba2a; color: #fff; }
.body { flex: 1; min-width: 0; }
.title { font-size: 15px; font-weight: 600; color: var(--el-color-primary); text-decoration: none; display: inline-block; }
.title:hover { text-decoration: underline; }
.meta { display: flex; gap: 12px; align-items: center; margin: 6px 0; flex-wrap: wrap; }
.dim { font-size: 12px; color: var(--el-text-color-secondary); }
.score-tag { font-weight: 600; }
.desc { font-size: 13px; color: var(--el-text-color-regular); margin: 0; line-height: 1.7; white-space: pre-wrap; }
.pager { display: flex; justify-content: center; margin-top: 20px; }

.daily-list { border-top: none; }
.daily-title { display: flex; gap: 12px; align-items: baseline; }
.daily-body {
  font-size: 13.5px; line-height: 1.9; white-space: pre-wrap;
  color: var(--el-text-color-regular);
}
.daily-link { margin-top: 10px; font-size: 12px; }
.daily-link a { color: var(--el-color-primary); text-decoration: none; }
</style>
