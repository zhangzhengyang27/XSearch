<template>
  <div>
    <div class="toolbar">
      <el-tabs v-model="tab" @tab-change="load">
        <el-tab-pane label="抖音热点榜" name="douyin_hot" />
        <el-tab-pane label="B站热门" name="bilibili_hot" />
        <el-tab-pane label="B站每周必看" name="bilibili_weekly" />
        <el-tab-pane label="豆瓣电影 Top250" name="douban_movie" />
        <el-tab-pane label="豆瓣图书 Top250" name="douban_book" />
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
      <div class="rank-no" :class="{ top: it.rank <= 3 }">{{ it.rank }}</div>
      <img v-if="it.front_image_url" :src="imgUrl(it.front_image_url)"
           referrerpolicy="no-referrer" loading="lazy"
           :class="['cover', tab.startsWith('douban') ? 'poster' : '']"
           @error="e => e.target.style.visibility = 'hidden'">
      <div class="body">
        <a :href="it.url" target="_blank" rel="noopener" class="title">{{ it.title }}</a>
        <div class="meta">
          <span v-if="it.rating != null">⭐ {{ it.rating }}</span>
          <span>👤 {{ it.author }}</span>
          <span v-if="tab === 'douyin_hot' && it.view_nums != null">🔥 {{ formatNum(it.view_nums) }} 热度</span>
          <span v-else-if="it.view_nums != null">▶ {{ formatNum(it.view_nums) }}</span>
          <span v-if="it.praise_nums != null">👍 {{ formatNum(it.praise_nums) }}</span>
          <span v-if="it.danmaku_nums != null">💬 {{ formatNum(it.danmaku_nums) }} 弹幕</span>
          <span v-if="it.create_date">📅 {{ it.create_date }}</span>
          <a v-if="tab.startsWith('bilibili')" class="cmt-link" @click.prevent="openComments(it)">查看评论</a>
        </div>
        <p class="desc" v-if="it.content">{{ it.content }}</p>
      </div>
    </article>

    <el-drawer v-model="drawer" :title="'评论 · ' + cmtTitle" size="380px">
      <div v-loading="cmtLoading">
        <el-alert v-if="cmtFetching" title="首次查看，正在抓取该视频评论（约 10-30 秒）…"
                  type="info" :closable="false" class="mb12" />
        <el-empty v-if="!cmtLoading && !comments.length"
                  description="该视频暂无入库评论，去「采集管理」页输入此 BV 号抓取" />
        <div v-for="(c, i) in comments" :key="i" class="cmt">
          <div class="cmt-head"><span class="ava">{{ (c.author || '匿')[0] }}</span>
            <b>{{ c.author }}</b><span class="dim" v-if="c.date">{{ c.date }}</span>
            <span class="dim">👍 {{ c.likes }}</span></div>
          <div class="cmt-body">{{ c.content }}</div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, imgUrl, formatNum } from '../api.js'

const drawer = ref(false)
const cmtTitle = ref('')
const comments = ref([])
const cmtLoading = ref(false)
const cmtFetching = ref(false)
let cmtBvid = ''

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// 打开抽屉：先查库；没有则自动按需抓取（游客模式每视频仅热评预览，
// 配置 BILI_COOKIE 后为全量），完成后自动刷新
async function openComments(it) {
  const bvid = (it.url || '').split('/video/')[1]?.split(/[/?]/)[0]
  if (!bvid) { ElMessage.warning('该条目没有视频链接'); return }
  cmtTitle.value = it.title
  cmtBvid = bvid
  comments.value = []
  drawer.value = true
  cmtLoading.value = true
  cmtFetching.value = false
  try {
    let d = await api.comments(bvid)
    if (!d.comments.length && !d.fetching) {
      cmtFetching.value = true
      await api.fetchComments(bvid)
      d = await api.comments(bvid)
    }
    while (d.fetching) {
      await sleep(3000)
      d = await api.comments(bvid)
    }
    comments.value = d.comments
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    cmtLoading.value = false
    cmtFetching.value = false
  }
}

const tab = ref('douyin_hot')
const items = ref([])
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const hint = ref('')
const crawl = ref({ running: false })
let pollTimer = null

const CRAWL_SPIDER = {
  douyin_hot: 'douyin_hot',
  bilibili_hot: 'bilibili_hot',
  bilibili_weekly: 'bilibili_weekly',
  douban_movie: 'douban_movie',
  douban_book: 'douban_book',
}

async function load() {
  loading.value = true
  error.value = ''
  hint.value = ''
  try {
    const d = await api.rankings(tab.value)
    items.value = d.items
    if (tab.value === 'bilibili_weekly' && d.total) hint.value = '每周必看为多期合集，同一视频可能出现在多期榜单中'
    else if (tab.value === 'douyin_hot' && d.total) hint.value = '抖音热点榜实时更新，点击「更新榜单数据」采集最新热点'
    else if (tab.value.startsWith('douban') && d.total) hint.value = '豆瓣 Top250 按评分排序，点击「更新榜单数据」可重新采集'
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function recrawl() {
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
.cover { width: 148px; height: 92px; object-fit: cover; border-radius: 6px; flex-shrink: 0; background: #f2f3f5; }
.cover.poster { width: 92px; height: 132px; }
.body { flex: 1; min-width: 0; }
.title { font-size: 15px; font-weight: 600; color: var(--el-color-primary); text-decoration: none; }
.title:hover { text-decoration: underline; }
.meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--el-text-color-secondary); margin: 6px 0; }
.desc { font-size: 13px; color: var(--el-text-color-regular); margin: 0; line-height: 1.6; }
.cmt-link { color: var(--el-color-primary); cursor: pointer; }
.cmt { margin-bottom: 14px; }
.cmt-head { display: flex; align-items: center; gap: 8px; font-size: 13px; margin-bottom: 6px; }
.ava { width: 24px; height: 24px; border-radius: 50%; background: var(--el-color-primary-light-7);
       color: var(--el-color-primary); display: inline-flex; align-items: center; justify-content: center; }
.dim { color: var(--el-text-color-secondary); font-size: 12px; }
.cmt-body { background: var(--el-fill-color-light); border-radius: 0 10px 10px 10px;
            padding: 8px 12px; font-size: 13px; line-height: 1.6; }
.mb12 { margin-bottom: 12px; }
</style>
