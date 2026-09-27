<template>
  <div>
    <div class="toolbar">
      <el-tabs v-model="tab" @tab-change="() => load(1)">
        <el-tab-pane label="AI 精选" name="selected" />
        <el-tab-pane label="AI 日报" name="daily" />
        <el-tab-pane label="AI 热点榜" name="hot" />
      </el-tabs>
      <el-button size="small" @click="load(page)" :loading="loading">刷新</el-button>
      <!-- 采集会拉起子进程，仅管理员可见（后端 /api/crawl/* 同样要求 X-Admin-Token） -->
      <el-button v-if="isAdmin" size="small" type="primary" @click="recrawl" :loading="starting"
                 :disabled="crawl.running">
        {{ crawl.running ? '采集中…' : '更新数据' }}
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />
    <el-alert v-if="hint" :title="hint" type="info" show-icon :closable="false" class="block" />
    <el-empty v-if="!loading && !items.length"
              :description="isAdmin ? '暂无数据，点右上角「更新数据」采集' : '暂无数据'" />

    <!-- AI 精选：feed 流（LLM 摘要 + 评分） -->
    <template v-if="tab === 'selected'">
      <article v-for="it in items" :key="it.url" class="news-card">
        <router-link :to="{ path: '/ai/detail', query: { id: it.id } }"
                     class="title">{{ it.title }}</router-link>
        <div class="meta">
          <el-tag v-if="it.rating != null" size="small" effect="dark" type="warning"
                  class="score-tag">AI 评分 {{ it.rating }}</el-tag>
          <span class="dim">📡 {{ it.author }}</span>
          <span v-if="it.create_date" class="dim">📅 {{ it.create_date }}</span>
        </div>
        <p class="desc" v-if="it.content">
          <el-tag class="ai-tag" size="small" type="info" effect="plain">AI 摘要</el-tag>{{ it.content }}
        </p>
      </article>
      <div class="pager" v-if="pageNums > 1">
        <el-pagination layout="prev, pager, next" :total="total" :page-size="20"
                       :current-page="page" @current-change="load" />
      </div>
    </template>

    <!-- AI 热点榜：排名卡（多信源印证 + AI 综述） -->
    <template v-else-if="tab === 'hot'">
      <article v-for="it in items" :key="it.url" class="rank-card">
        <div class="rank-no" :class="{ top: (it.rank ?? 99) <= 3 }">{{ it.rank }}</div>
        <div class="body">
          <router-link :to="{ path: '/ai/detail', query: { id: it.id } }"
                       class="title">{{ it.title }}</router-link>
          <div class="meta">
            <span v-if="it.view_nums != null">🔗 {{ it.view_nums }} 信源印证</span>
            <span v-if="it.create_date" class="dim">📅 {{ it.create_date }}</span>
          </div>
          <p class="desc" v-if="it.content">
            <el-tag class="ai-tag" size="small" type="info" effect="plain">AI 综述</el-tag>{{ it.content }}
          </p>
        </div>
      </article>
    </template>

    <!-- AI 日报：折叠面板，按日期展开全文 -->
    <template v-else>
      <el-collapse accordion v-model="openDaily" class="daily-list">
        <el-collapse-item v-for="d in parsedDailies" :key="d.url"
                          :name="d.create_date || d.title">
          <template #title>
            <div class="daily-head">
              <span class="daily-date">{{ d.create_date }}</span>
              <span class="daily-lead" v-if="d.parsed.leadTitle">{{ d.parsed.leadTitle }}</span>
            </div>
          </template>
          <div class="daily-body">
            <p v-for="(p, i) in d.parsed.leadTexts" :key="'p' + i" class="lead-text">{{ p }}</p>

            <section v-for="(sec, si) in d.parsed.sections" :key="'s' + si" class="daily-section">
              <div class="section-label">{{ sec.label }}</div>
              <div v-for="(item, ii) in sec.items" :key="ii" class="daily-item">
                <!-- 日报版块条目是从日报正文解析出来的，不是独立文档，
                     所以跳搜索而不是跳详情（跳详情只能靠标题猜，会带错文） -->
                <router-link :to="{ path: '/search', query: { q: item.title } }"
                             class="item-title">{{ item.title }}</router-link>
                <p v-if="item.summary" class="item-summary">{{ item.summary }}</p>
              </div>
            </section>

            <template v-if="d.parsed.flashes.length">
              <div class="section-label">快讯</div>
              <p v-for="(f, fi) in d.parsed.flashes" :key="'f' + fi" class="item-summary flash">{{ f }}</p>
            </template>

            <div class="daily-link">
              <a :href="d.url" target="_blank" rel="noopener">在 AIHOT 查看原文页 →</a>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errText, type DocItem } from '../api'
import { isAdmin } from '../auth'

const tab = ref('selected')
const items = ref<DocItem[]>([])
const total = ref(0)
const page = ref(1)
const pageNums = ref(0)
const loading = ref(false)
const error = ref('')
const hint = ref('')
const starting = ref(false)
const crawl = ref<{ running: boolean }>({ running: false })
const openDaily = ref('')
let pollTimer: ReturnType<typeof setTimeout> | undefined

const TAB_SOURCE: Record<string, string> = { selected: 'aihot_news', daily: 'aihot_daily', hot: 'aihot_hot' }
const HINTS: Record<string, string> = {
  selected: 'AIHOT 自动聚合数百个信源，由 LLM 摘要并打分精选（aihot.news 官方 API）',
  daily: 'AIHOT 每天 8:00（北京时间）发布精编日报，点击日期展开全文',
  hot: '过去 48 小时内被多个独立信源共同印证的 AI 事件',
}

// 日报解析结果：头条 / 导语段落 / 版块条目 / 快讯
interface DailyEntry { title: string; summary: string }
interface DailySection { label: string; items: DailyEntry[] }
interface DailyParsed { leadTitle: string; leadTexts: string[]; sections: DailySection[]; flashes: string[] }

// 日报正文是爬虫按固定格式生成的文本块（头条：/【版块】/· 条目/缩进摘要/快讯：），
// 在前端解析为结构化数据做富排版渲染
function parseDaily(text: string): DailyParsed {
  const out: DailyParsed = { leadTitle: '', leadTexts: [], sections: [], flashes: [] }
  let cur: DailySection | null = null
  for (const raw of (text || '').split('\n')) {
    const t = raw.trim()
    if (!t) continue
    if (t.startsWith('头条：') && !out.leadTitle) { out.leadTitle = t.slice(3).trim(); continue }
    if (t.startsWith('快讯：')) { out.flashes.push(t.slice(3).trim()); continue }
    const m = t.match(/^【(.+)】$/)
    if (m) { cur = { label: m[1], items: [] }; out.sections.push(cur); continue }
    if (t.startsWith('·')) {
      if (!cur) { cur = { label: '动态', items: [] }; out.sections.push(cur) }
      cur.items.push({ title: t.replace(/^·\s*/, ''), summary: '' })
      continue
    }
    if (cur && cur.items.length) {
      const last = cur.items[cur.items.length - 1]
      last.summary = last.summary ? last.summary + ' ' + t : t
    } else {
      out.leadTexts.push(t)
    }
  }
  return out
}

const parsedDailies = computed(() =>
  items.value.map(it => ({ ...it, parsed: parseDaily(it.content) })))

async function load(p = 1): Promise<void> {
  loading.value = true
  error.value = ''
  hint.value = HINTS[tab.value] || ''
  try {
    const d = await api.rankings(TAB_SOURCE[tab.value], p)
    items.value = d.items
    total.value = d.total
    page.value = d.page || p
    pageNums.value = d.page_nums || 0
    // 日报默认展开最新一期
    if (tab.value === 'daily' && items.value.length) {
      openDaily.value = items.value[0].create_date || items.value[0].title
    }
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

async function recrawl(): Promise<void> {
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
.toolbar :deep(.el-tabs) { flex: 1; min-width: 0; }
.toolbar :deep(.el-tabs__header) { margin-bottom: 0; }
/* 移动端：标签行独占一行，按钮换到下一行，避免被 tabs 挤出视口 */
@media (max-width: 768px) {
  .toolbar { flex-wrap: wrap; gap: 8px; }
  .toolbar :deep(.el-tabs) { flex: 1 1 100%; }
  .toolbar :deep(.el-tabs__item) { padding: 0 12px; font-size: 14px; }
}
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

/* ---- AI 日报：卡片化折叠面板 + 结构化排版 ---- */
.daily-list { border-top: none; }
.daily-list :deep(.el-collapse-item__header) {
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 6px 16px; margin-bottom: 10px;
  height: auto; min-height: 52px; line-height: 1.5;
  transition: box-shadow .2s;
}
.daily-list :deep(.el-collapse-item__header:hover) { box-shadow: var(--el-box-shadow-light); }
.daily-list :deep(.el-collapse-item__wrap) {
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; margin-bottom: 10px;
}
.daily-list :deep(.el-collapse-item__content) { padding: 18px 20px; }
.daily-head { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.daily-date {
  flex-shrink: 0; font-weight: 700; font-size: 15px;
  color: var(--el-text-color-primary);
}
.daily-lead {
  font-size: 13px; color: var(--el-text-color-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.daily-body { font-size: 13.5px; }
.lead-text {
  margin: 0 0 14px; padding: 10px 14px;
  background: var(--el-color-primary-light-9); border-radius: 8px;
  color: var(--el-text-color-regular); line-height: 1.8;
}
.daily-section { margin-bottom: 18px; }
.section-label {
  font-size: 12px; font-weight: 700; color: var(--el-color-primary);
  letter-spacing: 1px; margin-bottom: 10px;
  padding-left: 8px; border-left: 3px solid var(--el-color-primary);
  line-height: 1.2;
}
.daily-item { margin-bottom: 14px; }
.item-title {
  display: block; font-size: 14px; font-weight: 600;
  color: var(--el-text-color-primary); text-decoration: none; line-height: 1.6;
}
.item-title:hover { color: var(--el-color-primary); }
.item-summary {
  margin: 4px 0 0; padding-left: 12px;
  border-left: 2px solid var(--el-border-color-lighter);
  font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.8;
}
.item-summary.flash { margin-bottom: 8px; }
.daily-link { margin-top: 4px; font-size: 12px; }
.daily-link a { color: var(--el-color-primary); text-decoration: none; }
</style>
