<template>
  <div>
    <div class="search-bar">
      <el-input
        v-model="query"
        size="large"
        placeholder="搜新闻 / 文章"
        clearable
        @keyup.enter="doSearch(1)"
        :loading="loading"
      >
        <template #append>
          <el-button type="primary" @click="doSearch(1)" :loading="loading">搜索</el-button>
        </template>
      </el-input>
      <div class="suggest" v-if="suggestions.length && query">
        <div v-for="s in suggestions" :key="s" class="suggest-item" @click="pick(s)">{{ s }}</div>
      </div>
    </div>

    <!-- 排序 + 时间筛选 -->
    <div class="filter-bar" v-if="searched">
      <div class="filter-group">
        <span class="filter-label">排序</span>
        <el-radio-group v-model="sortBy" size="small" @change="() => doSearch(1)">
          <el-radio-button value="relevance">相关度</el-radio-button>
          <el-radio-button value="time">最新</el-radio-button>
          <el-radio-button value="hot">最热</el-radio-button>
        </el-radio-group>
      </div>
      <div class="filter-group">
        <span class="filter-label">时间</span>
        <el-check-tag v-for="d in DAY_OPTIONS" :key="d.value" size="small"
                      :checked="activeDays === d.value"
                      @change="() => setDays(d.value)">{{ d.label }}</el-check-tag>
      </div>
    </div>

    <!-- 来源分面（计数来自当前查询的聚合，点击即筛选） -->
    <div class="facet-bar" v-if="searched && facets.sources.length">
      <el-tag v-for="f in facets.sources" :key="f.key" effect="plain"
              :type="source === f.key ? 'primary' : 'info'"
              class="facet-tag" @click="switchSource(source === f.key ? '' : f.key)">
        {{ kindLabel(f.key) }} · {{ f.count }}
      </el-tag>
    </div>

    <el-alert v-if="corrected" type="warning" show-icon :closable="true" class="block"
              @close="corrected = ''"
              :title="`没有找到与「${lastQuery}」相关的结果，已为你显示「${corrected}」的结果`" />
    <el-alert v-else-if="fuzzyHit" type="info" show-icon :closable="true" class="block"
              @close="fuzzyHit = false"
              :title="`没有与「${lastQuery}」精确匹配的结果，已为你显示相近的结果`" />

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

    <div v-if="searched" class="meta">共 <b>{{ total }}</b> 条结果</div>

    <el-empty v-if="searched && !loading && !results.length" description="没有找到相关内容">
      <div v-if="hotWords.length" class="alt-suggest">
        <div class="alt-tip">大家都在搜</div>
        <el-tag v-for="s in hotWords" :key="s" class="alt-tag" effect="plain"
                style="cursor: pointer" @click="pick(s)">{{ s }}</el-tag>
      </div>
    </el-empty>

    <!-- 通用结果卡（新闻等） -->
    <article v-for="(r, i) in results" :key="i">
      <div class="card generic-card">
        <a :href="r.url" target="_blank" rel="noopener" class="card-title">
          <span v-html="renderHighlight(r.title)"></span>
          <el-tag v-if="r.source && r.source.startsWith('news_')" size="small"
                  effect="plain" type="primary" class="src-tag">{{ kindLabel(r) }}</el-tag>
        </a>
        <p class="card-content" v-html="renderHighlight(r.content)"></p>
        <div class="card-meta"><span v-if="r.author">👤 {{ r.author }}</span></div>
      </div>
    </article>

    <div class="pager" v-if="pageNums > 1">
      <el-pagination
        layout="prev, pager, next"
        :total="total" :page-size="10" :current-page="page"
        @current-change="doSearch" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, renderHighlight, errText, type DocItem, type Facets } from '../api'

const route = useRoute()

const query = ref('')
const source = ref('')
const results = ref<DocItem[]>([])
const total = ref(0)
const page = ref(1)
const pageNums = ref(0)
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const suggestions = ref<string[]>([])
const hotWords = ref<string[]>([])
const sortBy = ref('relevance')
const activeDays = ref('')
const facets = ref<Facets>({ sources: [], days: [] })
const corrected = ref('')
const lastQuery = ref('')
const fuzzyHit = ref(false)

const DAY_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部' },
  { value: '7', label: '近7天' },
  { value: '30', label: '近30天' },
  { value: '90', label: '近90天' },
]

const SOURCE_NAMES: Record<string, string> = {
  news_people: '人民网', news_chinanews: '中新网',
  news_ithome: 'IT之家', news_solidot: 'Solidot',
}
const PAGE_SIZE = 10  // 与后端 PAGE_SIZE 保持一致

function switchSource(key: string): void {
  source.value = key
  if (searched.value) doSearch(1)
}
function kindLabel(r: string | DocItem): string {
  const s = typeof r === 'string' ? r : r.source
  return SOURCE_NAMES[s] || s
}

let suggestTimer: ReturnType<typeof setTimeout> | undefined
let suggestSeq = 0  // 请求序号：防止旧请求的响应覆盖新结果（竞态条件）
watch(query, () => {
  clearTimeout(suggestTimer)
  suggestTimer = setTimeout(async () => {
    const seq = ++suggestSeq
    try {
      const result = await api.suggest(query.value)
      // 仅当本次请求是最新的一次时才更新结果，避免旧响应覆盖新结果
      if (seq === suggestSeq) {
        suggestions.value = result
      }
    } catch {
      if (seq === suggestSeq) {
        suggestions.value = []
      }
    }
  }, 250)
})

async function doSearch(p = 1): Promise<void> {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  suggestions.value = []
  try {
    const data = await api.search(query.value, p, source.value, sortBy.value, activeDays.value)
    // 分页 bug 修复：如果当前页返回 0 条结果且不是第 1 页，
    // 说明 ES 的 total 估算偏高导致出现空页，自动回退到第 1 页
    if (p > 1 && data.results.length === 0) {
      loading.value = false
      doSearch(1)
      return
    }
    results.value = data.results
    total.value = data.total
    page.value = data.page
    hotWords.value = data.suggestions || []
    facets.value = data.facets
    corrected.value = data.corrected || ''
    fuzzyHit.value = !!data.fuzzy && !data.corrected
    lastQuery.value = query.value
    // 分页 bug 修复：如果当前页结果数少于 PAGE_SIZE 且不是第 1 页，
    // 说明这是实际最后一页，修正 pageNums 避免出现空页
    if (p > 1 && data.results.length < PAGE_SIZE) {
      pageNums.value = p
    } else {
      pageNums.value = data.page_nums
    }
    searched.value = true
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

function pick(s: string): void {
  query.value = s
  doSearch(1)
}

function setDays(v: string): void {
  activeDays.value = activeDays.value === v ? '' : v
  if (searched.value) doSearch(1)
}

// 支持 /search?q=xxx 深链接（AI 详情页等入口跳转进来自动执行搜索）
watch(() => route.query.q, (v) => {
  const term = String(v || '').trim()
  if (!term) return
  query.value = term
  doSearch(1)
}, { immediate: true })
</script>

<style scoped>
.search-bar { position: relative; margin-bottom: 16px; }
.suggest {
  position: absolute; z-index: 20; left: 0; right: 0; top: 100%;
  background: #fff; border: 1px solid var(--el-border-color-light);
  border-radius: 0 0 8px 8px; box-shadow: var(--el-box-shadow-light);
}
.suggest-item { padding: 8px 16px; cursor: pointer; font-size: 14px; }
.suggest-item:hover { background: var(--el-fill-color-light); }
.block { margin-bottom: 16px; }
.meta { color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 12px; }

.card {
  display: flex; gap: 14px; background: #fff;
  border: 1px solid var(--el-border-color-lighter); border-radius: 10px;
  padding: 14px; margin-bottom: 12px;
  transition: box-shadow .2s;
}
.card:hover { box-shadow: var(--el-box-shadow-light); }
.media-body { flex: 1; min-width: 0; }
.card-title {
  font-size: 16px; font-weight: 600; color: var(--el-color-primary);
  text-decoration: none; display: inline-block; margin-bottom: 6px;
}
.card-title:hover { text-decoration: underline; }
.src-tag { margin-left: 8px; }
.card-meta {
  display: flex; gap: 14px; flex-wrap: wrap;
  font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px;
}
.card-content { font-size: 13.5px; color: var(--el-text-color-regular); line-height: 1.7; margin: 0; }
:deep(.kw) { color: #d03050; font-weight: 600; }

.pager { display: flex; justify-content: center; margin-top: 20px; }
.alt-suggest { margin-top: 4px; }
.alt-tip { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 10px; }
.alt-tag { margin: 0 8px 8px 0; }

/* 排序/时间筛选栏与来源分面 */
.filter-bar {
  display: flex; gap: 24px; align-items: center; flex-wrap: wrap;
  margin-bottom: 12px;
}
.filter-group { display: flex; align-items: center; gap: 8px; }
.filter-label { font-size: 12px; color: var(--el-text-color-secondary); }
.facet-bar { margin-bottom: 12px; }
.facet-tag { cursor: pointer; margin: 0 8px 6px 0; }
</style>
