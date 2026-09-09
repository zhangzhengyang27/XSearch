<template>
  <div>
    <div class="toolbar">
      <el-button size="small" @click="back">← 返回 AI</el-button>
    </div>

    <div v-loading="loading">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

      <!-- 本地命中的完整条目 -->
      <template v-if="best">
        <article class="detail-card">
          <h1 class="title">{{ best.title }}</h1>
          <div class="meta">
            <el-tag size="small" effect="dark" type="warning" v-if="best.rating != null">
              AI 评分 {{ best.rating }}
            </el-tag>
            <el-tag size="small" effect="plain" type="primary">{{ sourceName(best.source) }}</el-tag>
            <span class="dim" v-if="best.author">📡 {{ best.author }}</span>
            <span class="dim" v-if="best.create_date">📅 {{ best.create_date }}</span>
          </div>
          <div class="content">{{ best.content }}</div>
          <div class="actions">
            <a v-if="best.url" :href="best.url" target="_blank" rel="noopener">
              在 AIHOT 查看原文页 →
            </a>
          </div>
        </article>

        <!-- 其余本地相关条目 -->
        <div v-if="related.length" class="related">
          <div class="related-title">本地库中的相关条目</div>
          <div v-for="(r, i) in related" :key="i" class="related-item">
            <router-link :to="{ path: '/ai/detail', query: { q: r.title } }" class="r-title">
              {{ r.title }}
            </router-link>
            <span class="dim">{{ sourceName(r.source) }}</span>
          </div>
        </div>
      </template>

      <!-- 本地库未收录：降级提示 -->
      <el-empty v-else-if="!loading && !error"
                :description="`本地库中没有找到「${q}」的完整内容，可能尚未采集该条目`">
        <el-button type="primary" @click="$router.push({ path: '/ai' })">去 AI 页刷新数据</el-button>
      </el-empty>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errText, type DocItem } from '../api'

const route = useRoute()
const router = useRouter()
const q = computed(() => String(route.query.q || '').trim())
const loading = ref(false)
const error = ref('')
const items = ref<DocItem[]>([])

const best = computed<DocItem | null>(() => items.value[0] || null)
const related = computed(() => items.value.slice(1))

const SOURCE_NAMES: Record<string, string> = {
  aihot_news: 'AIHOT 精选', aihot_daily: 'AIHOT 日报', aihot_hot: 'AI 热点榜',
}
const sourceName = (s: string): string => SOURCE_NAMES[s] || s

async function load(): Promise<void> {
  if (!q.value) {
    items.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const d = await api.aiItem(q.value)
    items.value = d.items || []
  } catch (e) {
    error.value = errText(e)
  } finally {
    loading.value = false
  }
}

function back(): void {
  router.push({ path: '/ai' })
}

watch(q, () => load())
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
