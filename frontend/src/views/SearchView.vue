<template>
  <div>
    <div class="search-bar">
      <el-input
        v-model="query"
        size="large"
        placeholder="搜电影 / 图书（B站榜单在「榜单」页，评论在榜单视频卡内）"
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

    <!-- 来源 Tab 切换 -->
    <div class="source-tabs" v-if="searched">
      <div
        v-for="(label, key) in TAB_SOURCES"
        :key="key"
        class="source-tab"
        :class="{ active: source === key }"
        @click="switchSource(key)"
      >{{ label }}</div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

    <div v-if="searched" class="meta">共 <b>{{ total }}</b> 条结果</div>

    <el-empty v-if="searched && !loading && !results.length" description="没有找到相关内容" />

    <!-- 豆瓣电影 / 图书：海报卡 -->
    <article v-for="(r, i) in results" :key="i">
      <div v-if="kind(r) === 'douban'" class="card media-card">
        <img v-if="r.front_image_url" :src="imgUrl(r.front_image_url)" referrerpolicy="no-referrer"
             loading="lazy" class="poster" @error="hideImg">
        <div class="media-body">
          <a :href="r.url" target="_blank" rel="noopener" class="card-title">
            <span v-html="renderHighlight(r.title)"></span>
            <span class="stars" v-if="r.rating">⭐ {{ r.rating }}</span>
            <el-tag v-if="r.rank" size="small" effect="plain" type="warning">No.{{ r.rank }}</el-tag>
          </a>
          <div class="card-meta">
            <span>{{ kindLabel(r) }}</span>
            <span v-if="r.author"> {{ r.source === 'douban_movie' ? '🎬' : '📖' }} {{ r.author }}</span>
          </div>
          <p class="card-content" v-html="renderHighlight(r.content)"></p>
        </div>
      </div>

      <!-- B站视频：封面卡 -->
      <div v-else-if="kind(r) === 'video'" class="card media-card">
        <img v-if="r.front_image_url" :src="imgUrl(r.front_image_url)" referrerpolicy="no-referrer"
             loading="lazy" class="cover" @error="hideImg">
        <div class="media-body">
          <a :href="r.url" target="_blank" rel="noopener" class="card-title">
            <span v-html="renderHighlight(r.title)"></span>
            <el-tag size="small" effect="plain" type="danger" class="src-tag">{{ kindLabel(r) }}</el-tag>
          </a>
          <div class="card-meta">
            <span v-if="r.author">👤 {{ r.author }}</span>
            <span v-if="r.view_nums != null">▶ {{ formatNum(r.view_nums) }}</span>
            <span v-if="r.praise_nums != null">👍 {{ formatNum(r.praise_nums) }}</span>
            <span v-if="r.danmaku_nums != null">💬 {{ formatNum(r.danmaku_nums) }} 弹幕</span>
            <span v-if="r.create_date">📅 {{ r.create_date }}</span>
          </div>
          <p class="card-content" v-if="r.content" v-html="renderHighlight(r.content)"></p>
        </div>
      </div>

      <!-- B站评论：气泡体 -->
      <div v-else-if="kind(r) === 'comment'" class="card comment-card">
        <div class="comment-head">
          <span class="avatar">{{ (r.author || '匿')[0] }}</span>
          <b>{{ r.author }}</b>
          <span class="dim" v-if="r.create_date">{{ r.create_date }}</span>
          <span class="dim" v-if="r.praise_nums">👍 {{ formatNum(r.praise_nums) }}</span>
          <a :href="r.url" target="_blank" rel="noopener" class="ctx">来源视频</a>
        </div>
        <p class="comment-body" v-html="renderHighlight(r.content)"></p>
      </div>

      <!-- 网易云音乐：歌曲卡 -->
      <div v-else-if="kind(r) === 'music'" class="card media-card">
        <div class="music-icon">♪</div>
        <div class="media-body">
          <a :href="r.url" target="_blank" rel="noopener" class="card-title">
            <span v-html="renderHighlight(r.title)"></span>
            <el-tag size="small" effect="plain" type="success" class="src-tag">网易云音乐</el-tag>
          </a>
          <div class="card-meta">
            <span v-if="r.author">🎤 {{ r.author }}</span>
            <span v-if="r.duration">⏱ {{ Math.floor(r.duration/60) }}:{{ String(r.duration%60).padStart(2,'0') }}</span>
          </div>
          <p class="card-content" v-if="r.content" v-html="renderHighlight(r.content)"></p>
        </div>
      </div>

      <!-- 掘金技术文章：文章卡 -->
      <div v-else-if="kind(r) === 'article'" class="card generic-card">
        <a :href="r.url" target="_blank" rel="noopener" class="card-title">
          <span v-html="renderHighlight(r.title)"></span>
          <el-tag size="small" effect="plain" type="warning" class="src-tag">掘金文章</el-tag>
        </a>
        <p class="card-content" v-html="renderHighlight(r.content)"></p>
        <div class="card-meta"><span v-if="r.author">✍ {{ r.author }}</span></div>
      </div>

      <!-- 通用（演示站等） -->
      <div v-else class="card generic-card">
        <a :href="r.url" target="_blank" rel="noopener" class="card-title">
          <span v-html="renderHighlight(r.title)"></span>
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

<script setup>
import { ref, watch } from 'vue'
import { api, renderHighlight, formatNum, imgUrl } from '../api.js'

const query = ref('')
const source = ref('')
const results = ref([])
const total = ref(0)
const page = ref(1)
const pageNums = ref(0)
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const suggestions = ref([])

const SOURCE_NAMES = {
  all: '全网搜索(实时)',
  douban_movie: '豆瓣电影', douban_book: '豆瓣图书',
  bilibili_video: 'B站视频', juejin_article: '掘金文章',
}
// Tab 切换的来源列表（"全部"为空字符串走 ES 库内检索；"全网"为 all 走实时聚合）
const TAB_SOURCES = {
  '': '全部',
  all: '全网(实时)',
  bilibili_video: 'B站视频',
  netease_music: '网易云音乐',
  douban_movie: '豆瓣电影',
  douban_book: '豆瓣图书',
  juejin_article: '掘金文章',
}
const PAGE_SIZE = 10  // 与后端 PAGE_SIZE 保持一致

function switchSource(key) {
  source.value = key
  if (searched.value) doSearch(1)
}
const kindLabel = (r) => SOURCE_NAMES[r.source] || r.source
// 实体类型 -> 卡片模板
const kind = (r) => {
  if (r.source === 'bilibili_comments') return 'comment'
  if (r.source && r.source.startsWith('bilibili_')) return 'video'
  if (r.source && r.source.startsWith('douban_')) return 'douban'
  if (r.source === 'netease_music') return 'music'
  if (r.source === 'juejin_article') return 'article'
  return 'generic'
}
const hideImg = (e) => { e.target.style.display = 'none' }

let suggestTimer = null
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

async function doSearch(p = 1) {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  suggestions.value = []
  try {
    const data = await api.search(query.value, p, source.value)
    // 分页 bug 修复：如果当前页返回 0 条结果且不是第 1 页，
    // 说明 ES 的 total 估算偏高导致出现空页，自动回退到第 1 页
    if (p > 1 && (!data.results || data.results.length === 0)) {
      loading.value = false
      doSearch(1)
      return
    }
    results.value = data.results
    total.value = data.total
    page.value = data.page
    // 分页 bug 修复：如果当前页结果数少于 PAGE_SIZE 且不是第 1 页，
    // 说明这是实际最后一页，修正 pageNums 避免出现空页
    if (p > 1 && data.results && data.results.length < PAGE_SIZE) {
      pageNums.value = p
    } else {
      pageNums.value = data.page_nums
    }
    searched.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function pick(s) {
  query.value = s
  doSearch(1)
}
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
.poster { width: 92px; height: 132px; object-fit: cover; border-radius: 6px; flex-shrink: 0; background: #f2f3f5; }
.cover { width: 176px; height: 108px; object-fit: cover; border-radius: 6px; flex-shrink: 0; background: #f2f3f5; }
.media-body { flex: 1; min-width: 0; }
.card-title {
  font-size: 16px; font-weight: 600; color: var(--el-color-primary);
  text-decoration: none; display: inline-block; margin-bottom: 6px;
}
.card-title:hover { text-decoration: underline; }
.stars { color: #f7ba2a; font-size: 13px; margin: 0 8px; }
.src-tag { margin-left: 8px; }
.card-meta {
  display: flex; gap: 14px; flex-wrap: wrap;
  font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 6px;
}
.card-content { font-size: 13.5px; color: var(--el-text-color-regular); line-height: 1.7; margin: 0; }
:deep(.kw) { color: #d03050; font-weight: 600; }

.music-icon {
  width: 64px; height: 64px; border-radius: 10px; flex-shrink: 0;
  background: linear-gradient(135deg, #22c55e, #16a34a); color: #fff;
  font-size: 30px; display: flex; align-items: center; justify-content: center;
}
.comment-card { flex-direction: column; gap: 8px; }
.comment-head { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.avatar {
  width: 28px; height: 28px; border-radius: 50%; background: var(--el-color-primary-light-7);
  color: var(--el-color-primary); display: inline-flex; align-items: center; justify-content: center;
  font-weight: 600;
}
.dim { color: var(--el-text-color-secondary); font-size: 12px; }
.ctx { margin-left: auto; font-size: 12px; }
.comment-body {
  margin: 0; background: var(--el-fill-color-light); border-radius: 0 10px 10px 10px;
  padding: 10px 14px; font-size: 14px; line-height: 1.7;
}
.pager { display: flex; justify-content: center; margin-top: 20px; }

/* 来源 Tab 切换 */
.source-tabs {
  display: flex; gap: 4px; margin-bottom: 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  overflow-x: auto;
}
.source-tab {
  padding: 8px 16px; cursor: pointer; font-size: 14px;
  color: var(--el-text-color-secondary);
  border-bottom: 2px solid transparent;
  white-space: nowrap; transition: all .2s;
  margin-bottom: -1px;
}
.source-tab:hover { color: var(--el-color-primary); }
.source-tab.active {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
  font-weight: 600;
}
</style>
