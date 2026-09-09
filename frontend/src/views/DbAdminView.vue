<template>
  <div>
    <!-- 索引概览 -->
    <el-card shadow="never" class="block">
      <template #header>
        <b>索引概览</b>
        <el-button size="small" link style="float: right" @click="loadOverview">刷新</el-button>
      </template>
      <div class="stats-overview">
        <div class="stat-card">
          <div class="stat-num">{{ overview.total }}</div>
          <div class="stat-label">文档总数</div>
        </div>
        <div class="stat-card primary">
          <div class="stat-num">{{ formatSize(overview.size_bytes) }}</div>
          <div class="stat-label">索引大小</div>
        </div>
        <div class="stat-card">
          <div class="stat-num">{{ overview.by_source.length }}</div>
          <div class="stat-label">来源数</div>
        </div>
      </div>
      <div class="source-tags">
        <el-tag v-for="s in overview.by_source" :key="s.key" size="large"
                :type="s.key === filters.source ? 'primary' : 'info'"
                effect="plain" class="source-tag"
                @click="filterBySource(s.key)">
          {{ s.key }} · {{ s.count }}
        </el-tag>
      </div>
    </el-card>

    <!-- 文档浏览 -->
    <el-card shadow="never">
      <template #header>
        <b>文档浏览</b>
        <span class="hint">管理视角可见全部来源（含榜单数据）</span>
      </template>
      <div class="toolbar">
        <el-select v-model="filters.source" placeholder="全部来源" clearable
                   style="width: 200px" @change="load(1)">
          <el-option v-for="s in overview.by_source" :key="s.key"
                     :label="s.key" :value="s.key" />
        </el-select>
        <el-input v-model="filters.q" placeholder="搜索标题/正文" clearable
                  style="width: 260px" @keyup.enter="load(1)" @clear="load(1)" />
        <el-button type="primary" @click="load(1)" :loading="loading">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>

      <el-table :data="items" v-loading="loading" size="small" @row-click="openDetail">
        <el-table-column label="标题" min-width="260">
          <template #default="{ row }">
            <a :href="row.url" target="_blank" rel="noopener" class="title"
               @click.stop>{{ row.title }}</a>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="作者" prop="author" width="120" show-overflow-tooltip />
        <el-table-column label="热度" width="90">
          <template #default="{ row }">
            {{ row.view_nums != null ? formatNum(row.view_nums) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="发布日期" prop="create_date" width="110" />
        <el-table-column label="采集时间" prop="crawled_at" width="160" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click.stop="openDetail(row)">详情</el-button>
            <el-button size="small" link type="danger" @click.stop="removeDoc(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager" v-if="pageNums > 1">
        <el-pagination layout="prev, pager, next" :total="total" :page-size="20"
                       :current-page="page" @current-change="load" />
      </div>
    </el-card>

    <!-- 详情 / 编辑抽屉 -->
    <el-drawer v-model="drawerVisible" :title="editing ? '编辑文档' : '文档详情'" size="45%">
      <template v-if="doc">
        <div class="drawer-actions" v-if="!editing">
          <el-button type="primary" size="small" @click="startEdit">编辑</el-button>
          <el-button type="danger" size="small" @click="removeDoc(doc)">删除</el-button>
        </div>

        <template v-if="!editing">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="ID"><code class="doc-id">{{ doc.id }}</code></el-descriptions-item>
            <el-descriptions-item label="标题">{{ doc.title }}</el-descriptions-item>
            <el-descriptions-item label="来源">{{ doc.source }}</el-descriptions-item>
            <el-descriptions-item label="作者">{{ doc.author || '-' }}</el-descriptions-item>
            <el-descriptions-item label="URL">
              <a :href="doc.url" target="_blank" rel="noopener" class="title">{{ doc.url }}</a>
            </el-descriptions-item>
            <el-descriptions-item label="发布日期">{{ doc.create_date || '-' }}</el-descriptions-item>
            <el-descriptions-item label="采集时间">{{ doc.crawled_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="热度">{{ doc.view_nums ?? '-' }}</el-descriptions-item>
            <el-descriptions-item label="标签">{{ Array.isArray(doc.tags) ? doc.tags.join('、') : (doc.tags || '-') }}</el-descriptions-item>
            <el-descriptions-item label="正文">
              <p class="doc-content">{{ doc.content }}</p>
            </el-descriptions-item>
          </el-descriptions>
        </template>

        <el-form v-else label-width="70px">
          <el-form-item label="标题">
            <el-input v-model="editForm.title" />
          </el-form-item>
          <el-form-item label="作者">
            <el-input v-model="editForm.author" />
          </el-form-item>
          <el-form-item label="标签">
            <el-input v-model="editForm.tagsStr" placeholder="多个标签用英文逗号分隔" />
          </el-form-item>
          <el-form-item label="正文">
            <el-input v-model="editForm.content" type="textarea" :rows="14" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
            <el-button @click="editing = false">取消</el-button>
          </el-form-item>
        </el-form>
      </template>
    </el-drawer>

    <!-- 危险操作 -->
    <el-card shadow="never" class="block">
      <template #header><b style="color: var(--el-color-danger)">危险操作</b></template>
      <el-alert type="warning" :closable="false" show-icon class="block"
                title="按来源批量清理将永久删除该来源的全部文档，且不可恢复" />
      <div class="toolbar">
        <el-select v-model="purgeSource" placeholder="选择要清理的来源" style="width: 220px">
          <el-option v-for="s in overview.by_source" :key="s.key"
                     :label="`${s.key}（${s.count} 条）`" :value="s.key" />
        </el-select>
        <el-input v-model="purgeConfirm" placeholder="请输入来源名以确认" style="width: 220px" clearable />
        <el-button type="danger" :loading="purging"
                   :disabled="!purgeSource || purgeConfirm !== purgeSource"
                   @click="doPurge">清理该来源全部数据</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  api, formatNum, errText, type DbDocRow, type DbDocUpdateFields,
  type DbDocsResult, type DbOverviewResult,
} from '../api'

// ---- 索引概览 ----
const overview = ref<DbOverviewResult>({ total: 0, size_bytes: 0, by_source: [] })

async function loadOverview(): Promise<void> {
  try {
    overview.value = await api.dbOverview()
  } catch (e) {
    ElMessage.error(errText(e))
  }
}

function formatSize(bytes: number): string {
  if (!bytes) return '0'
  return bytes >= 1024 * 1024 ? (bytes / 1024 / 1024).toFixed(1) + ' MB' : Math.ceil(bytes / 1024) + ' KB'
}

// ---- 文档浏览 ----
const filters = reactive({ source: '', q: '' })
const items = ref<DbDocRow[]>([])
const total = ref(0)
const page = ref(1)
const pageNums = ref(0)
const loading = ref(false)

async function load(p = page.value): Promise<void> {
  loading.value = true
  try {
    const d: DbDocsResult = await api.dbDocs(filters.source, filters.q.trim(), p)
    items.value = d.items
    total.value = d.total
    page.value = d.page
    pageNums.value = d.page_nums
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    loading.value = false
  }
}

function filterBySource(key: string): void {
  filters.source = filters.source === key ? '' : key
  load(1)
}

function resetFilters(): void {
  filters.source = ''
  filters.q = ''
  load(1)
}

// ---- 详情 / 编辑抽屉 ----
const drawerVisible = ref(false)
const doc = ref<DbDocRow | null>(null)
const editing = ref(false)
const saving = ref(false)
const editForm = reactive({ title: '', author: '', tagsStr: '', content: '' })

async function openDetail(row: { id: string }): Promise<void> {
  try {
    doc.value = await api.dbDoc(row.id)
    editing.value = false
    drawerVisible.value = true
  } catch (e) {
    ElMessage.error(errText(e))
  }
}

function startEdit(): void {
  const d = doc.value
  if (!d) return
  editForm.title = d.title || ''
  editForm.author = d.author || ''
  editForm.tagsStr = Array.isArray(d.tags) ? d.tags.join(',') : (d.tags || '')
  editForm.content = d.content || ''
  editing.value = true
}

async function saveEdit(): Promise<void> {
  if (!editForm.title.trim()) {
    ElMessage.warning('标题不能为空')
    return
  }
  if (!doc.value) return
  const fields: DbDocUpdateFields = {
    title: editForm.title.trim(),
    author: editForm.author.trim(),
    tags: editForm.tagsStr.split(',').map(t => t.trim()).filter(Boolean),
    content: editForm.content,
  }
  saving.value = true
  try {
    await api.dbDocUpdate(doc.value.id, fields)
    ElMessage.success('已保存')
    editing.value = false
    await openDetail({ id: doc.value.id })  // 重新拉取展示最新全文
    load()
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    saving.value = false
  }
}

async function removeDoc(row: DbDocRow): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.title}」？此操作不可恢复`, '删除文档',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch {
    return
  }
  try {
    await api.dbDocDelete(row.id)
    ElMessage.success('已删除')
    drawerVisible.value = false
    loadOverview()
    load()
  } catch (e) {
    ElMessage.error(errText(e))
  }
}

// ---- 危险操作：按来源清理 ----
const purgeSource = ref('')
const purgeConfirm = ref('')
const purging = ref(false)

async function doPurge(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `将永久删除来源「${purgeSource.value}」的全部文档，不可恢复！`, '批量清理',
      { type: 'error', confirmButtonText: '永久删除', cancelButtonText: '取消' })
  } catch {
    return
  }
  purging.value = true
  try {
    const r = await api.dbPurge(purgeSource.value)
    ElMessage.success(`已删除 ${r.deleted} 条文档`)
    purgeSource.value = ''
    purgeConfirm.value = ''
    loadOverview()
    load(1)
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    purging.value = false
  }
}

onMounted(() => {
  loadOverview()
  load(1)
})
</script>

<style scoped>
.block { margin-bottom: 16px; }
.hint { font-weight: 400; font-size: 12px; color: var(--el-text-color-secondary); margin-left: 8px; }
.stats-overview { display: flex; gap: 12px; margin-bottom: 14px; }
.stat-card { flex: 1; text-align: center; padding: 14px 8px; border-radius: 8px; background: var(--el-fill-color-light); }
.stat-card.primary { background: var(--el-color-primary-light-9); }
.stat-num { font-size: 24px; font-weight: 700; color: var(--el-text-color-primary); }
.stat-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.source-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.source-tag { cursor: pointer; }
.toolbar { display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.title { font-size: 13px; font-weight: 600; color: var(--el-color-primary); text-decoration: none; }
.title:hover { text-decoration: underline; }
.pager { display: flex; justify-content: center; margin-top: 12px; }
.drawer-actions { margin-bottom: 14px; }
.doc-id { word-break: break-all; }
.doc-content { white-space: pre-wrap; line-height: 1.7; margin: 0; }
</style>
