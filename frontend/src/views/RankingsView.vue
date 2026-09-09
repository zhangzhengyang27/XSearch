<template>
  <div>
    <div class="toolbar">
      <el-tabs v-model="tab" @tab-change="load">
        <el-tab-pane label="抖音热点榜" name="douyin_hot" />
      </el-tabs>
      <el-button size="small" @click="load" :loading="loading">刷新列表</el-button>
      <el-button size="small" type="primary" @click="recrawl" :loading="starting"
                 :disabled="crawl.running">
        {{ crawl.running ? '采集中…' : '更新榜单数据' }}
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />
    <el-alert v-if="hint" :title="hint" type="info" show-icon :closable="false" class="block" />

    <el-empty v-if="!loading && !items.length" description="暂无数据，点右上角「更新榜单数据」采集" />

    <article v-for="it in items" :key="it.url" class="rank-card">
      <div class="rank-no" :class="{ top: (it.rank ?? 99) <= 3 }">{{ it.rank }}</div>
      <div class="body">
        <a :href="it.url" target="_blank" rel="noopener" class="title">{{ it.title }}</a>
        <div class="meta">
          <span v-if="it.rating != null">⭐ {{ it.rating }}</span>
          <span>👤 {{ it.author }}</span>
          <span v-if="tab === 'aihot_hot' && it.view_nums != null">🔗 {{ it.view_nums }} 信源印证</span>
          <span v-else-if="tab === 'douyin_hot' && it.view_nums != null">🔥 {{ formatNum(it.view_nums) }} 热度</span>
          <span v-if="it.create_date">📅 {{ it.create_date }}</span>
        </div>
        <p class="desc" v-if="it.content">{{ it.content }}</p>
      </div>
    </article>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, formatNum, errText, type DocItem } from '../api'

const tab = ref('douyin_hot')
const items = ref<DocItem[]>([])
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const hint = ref('')
const crawl = ref<{ running: boolean }>({ running: false })
let pollTimer: ReturnType<typeof setTimeout> | undefined

const CRAWL_SPIDER: Record<string, string> = {
  douyin_hot: 'douyin_hot',
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  hint.value = ''
  try {
    const d = await api.rankings(tab.value)
    items.value = d.items
    if (tab.value === 'douyin_hot' && d.total) hint.value = '抖音热点榜实时更新，点击「更新榜单数据」采集最新热点'
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

async function recrawl(): Promise<void> {
  starting.value = true
  try {
    const r = await api.crawlStart(CRAWL_SPIDER[tab.value], 1, false)
    if (r.started) {
      ElMessage.success('采集任务已启动，完成后点"刷新列表"')
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
        ? '采集结束但没有抓到数据，可能被目标站风控拦截'
        : '采集完成，点"刷新列表"查看新数据')
      return
    }
  } catch { /* 忽略轮询失败 */ }
  pollTimer = setTimeout(poll, 3000)
}

onMounted(load)
onUnmounted(() => clearTimeout(pollTimer))
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.toolbar :deep(.el-tabs) { flex: 1; }
.toolbar :deep(.el-tabs__header) { margin-bottom: 0; }
.block { margin-bottom: 12px; }
.rank-card {
  display: flex; gap: 14px; align-items: flex-start;
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 12px 14px; margin-bottom: 10px;
}
.rank-no {
  width: 34px; height: 34px; border-radius: 8px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; background: var(--el-fill-color); color: var(--el-text-color-secondary);
}
.rank-no.top { background: #f7ba2a; color: #fff; }
.body { flex: 1; min-width: 0; }
.title { font-size: 15px; font-weight: 600; color: var(--el-color-primary); text-decoration: none; }
.title:hover { text-decoration: underline; }
.meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--el-text-color-secondary); margin: 6px 0; }
.desc { font-size: 13px; color: var(--el-text-color-regular); margin: 0; line-height: 1.6; }
</style>
