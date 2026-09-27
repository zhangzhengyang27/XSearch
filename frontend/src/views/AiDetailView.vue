<template>
  <div>
    <div class="toolbar">
      <el-button size="small" @click="back">← 返回 AI</el-button>
    </div>

    <div v-loading="loading">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

      <!-- 按 ES 文档 _id 精确取文：不会再出现"点进去是另一篇" -->
      <article v-if="doc" class="detail-card">
        <h1 class="title">{{ doc.title }}</h1>
        <div class="meta">
          <el-tag size="small" effect="dark" type="warning" v-if="doc.rating != null">
            AI 评分 {{ doc.rating }}
          </el-tag>
          <el-tag size="small" effect="plain" type="primary">{{ sourceName(doc.source) }}</el-tag>
          <span class="dim" v-if="doc.author">📡 {{ doc.author }}</span>
          <span class="dim" v-if="doc.create_date">📅 {{ doc.create_date }}</span>
        </div>
        <div class="content">
          <el-tag class="ai-tag" size="small" type="info" effect="plain">AI 摘要</el-tag>{{ doc.content }}
        </div>
        <div class="actions">
          <a v-if="doc.url" :href="doc.url" target="_blank" rel="noopener">
            查看原文 →
          </a>
        </div>
      </article>

      <!-- id 缺失或文档已被清理：明确说清楚，而不是猜一篇给用户 -->
      <el-empty v-else-if="!loading && !error"
                :description="notFound ? '没有找到这篇文章，可能已被清理或链接不完整' : '缺少文章编号，请从列表进入'">
        <el-button type="primary" @click="$router.push({ path: '/ai' })">回到 AI 讯息</el-button>
      </el-empty>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errText, ApiError, type DocItem } from '../api'

const route = useRoute()
const router = useRouter()
const docId = () => String(route.query.id || '').trim()
const loading = ref(false)
const error = ref('')
const notFound = ref(false)
const doc = ref<DocItem | null>(null)

const SOURCE_NAMES: Record<string, string> = {
  aihot_news: 'AIHOT 精选', aihot_daily: 'AIHOT 日报', aihot_hot: 'AI 热点榜',
}
const sourceName = (s: string): string => SOURCE_NAMES[s] || s

let seq = 0  // 只认最新一次请求：换 id 时旧响应不能覆盖新文档
async function load(): Promise<void> {
  const id = docId()
  if (!id) {
    doc.value = null
    error.value = ''
    notFound.value = false   // 链接本身不完整，与"文档已删除"是两回事
    return
  }
  const my = ++seq
  loading.value = true
  error.value = ''
  notFound.value = false
  try {
    const d = await api.doc(id)
    if (my === seq) doc.value = d
  } catch (e) {
    if (my !== seq) return
    doc.value = null
    // 404 是"这篇文章没了"，该给空态而不是红色故障提示；其余才是真出错
    if (e instanceof ApiError && (e.status === 404 || e.code === 'not_found')) {
      notFound.value = true
    } else {
      error.value = errText(e)
    }
  } finally {
    if (my === seq) loading.value = false
  }
}

function back(): void {
  router.push({ path: '/ai' })
}

watch(() => route.query.id, () => load())
onMounted(load)
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.block { margin-bottom: 12px; }
.detail-card {
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px; padding: 20px 24px;
}
.title { font-size: 20px; margin: 0 0 12px; line-height: 1.5; color: var(--el-text-color-primary); }
.meta { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
.dim { font-size: 12px; color: var(--el-text-color-secondary); }
.content { font-size: 14px; line-height: 1.9; white-space: pre-wrap; color: var(--el-text-color-regular); }
.actions { margin-top: 16px; font-size: 13px; }
.actions a { color: var(--el-color-primary); text-decoration: none; }
.actions a:hover { text-decoration: underline; }

.related { margin-top: 20px; }
.related-title { font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 8px; }
.related-item {
  display: flex; justify-content: space-between; gap: 12px; align-items: baseline;
  background: #fff; border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px; padding: 10px 14px; margin-bottom: 8px;
}
.r-title { font-size: 13.5px; color: var(--el-color-primary); text-decoration: none; }
.r-title:hover { text-decoration: underline; }
</style>
