<template>
  <div>
    <el-card shadow="never" class="block">
      <template #header><b>启动采集</b></template>
      <el-form label-width="120px">
        <el-form-item label="选择爬虫">
          <el-radio-group v-model="spider">
            <el-radio-button v-for="s in spiders" :key="s.key" :value="s.key">
              {{ s.label }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="start" :loading="starting" :disabled="status.running">
            {{ status.running ? '任务运行中…' : '开始采集' }}
          </el-button>
        </el-form-item>
      </el-form>
      <div class="tip">
        采集的数据经管道写入 Elasticsearch quotes 索引，完成后即可在"搜索"和"数据概览"看到。
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <b>任务状态</b>
        <el-tag :type="status.running ? 'warning' : 'info'" size="small" class="tag">
          {{ status.running ? '运行中' : '空闲' }}
        </el-tag>
        <span v-if="status.started_at" class="started">开始于 {{ status.started_at }}</span>
      </template>
      <pre class="log">{{ status.log_tail.join('\n') || '暂无日志' }}</pre>
    </el-card>

    <!-- 定时任务管理 -->
    <el-card shadow="never" class="block">
      <template #header>
        <b>定时任务</b>
        <el-tag v-if="!scheduleData.scheduler_active" type="warning" size="small" class="tag">
          APScheduler 未安装，任务不会自动触发
        </el-tag>
        <span class="hint">cron 格式：分 时 日 月 周（如 "0 8 * * *" = 每天8点）</span>
      </template>

      <!-- 添加任务表单 -->
      <el-form :inline="true" class="schedule-form">
        <el-form-item label="爬虫">
          <el-select v-model="newSchedule.spider" style="width: 160px">
            <el-option v-for="s in spiders" :key="s.key" :label="s.label" :value="s.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="Cron">
          <el-input v-model="newSchedule.cron" placeholder="0 8 * * *" style="width: 160px" clearable />
        </el-form-item>
        <el-form-item>
          <el-button-group>
            <el-button size="small" @click="newSchedule.cron = '0 8 * * *'">每天8点</el-button>
            <el-button size="small" @click="newSchedule.cron = '0 */6 * * *'">每6小时</el-button>
            <el-button size="small" @click="newSchedule.cron = '*/30 * * * *'">每30分钟</el-button>
            <el-button size="small" @click="newSchedule.cron = '0 9 * * 1'">每周一9点</el-button>
          </el-button-group>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="addSchedule" :loading="addingSchedule">添加定时任务</el-button>
        </el-form-item>
      </el-form>

      <!-- 任务列表 -->
      <el-table :data="scheduleData.jobs" size="small" v-if="scheduleData.jobs?.length" style="margin-top: 8px">
        <el-table-column label="爬虫" prop="label" width="140" />
        <el-table-column label="Cron" prop="cron" width="140" />
        <el-table-column label="下次运行" prop="next_run" width="170">
          <template #default="{ row }">
            <span v-if="row.enabled && row.next_run">{{ row.next_run }}</span>
            <span v-else-if="!row.enabled" class="dim">已禁用</span>
            <span v-else class="dim">-</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" prop="created_at" width="170" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" size="small" @change="(v: boolean | string | number) => toggleSchedule(row.id, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button type="primary" size="small" link @click="openEditSchedule(row)">编辑</el-button>
            <el-button type="danger" size="small" link @click="removeSchedule(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无定时任务" :image-size="60" />

      <!-- 编辑定时任务 -->
      <el-dialog v-model="editDialog.visible" title="编辑定时任务" width="420px">
        <el-form label-width="80px">
          <el-form-item label="爬虫">
            <el-select v-model="editDialog.spider" style="width: 100%">
              <el-option v-for="s in spiders" :key="s.key" :label="s.label" :value="s.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="Cron">
            <el-input v-model="editDialog.cron" placeholder="分 时 日 月 周" clearable />
          </el-form-item>
          <el-form-item>
            <el-button-group>
              <el-button size="small" @click="editDialog.cron = '0 8 * * *'">每天8点</el-button>
              <el-button size="small" @click="editDialog.cron = '10 8 * * *'">8:10</el-button>
              <el-button size="small" @click="editDialog.cron = '20 8 * * *'">8:20</el-button>
              <el-button size="small" @click="editDialog.cron = '0 */6 * * *'">每6小时</el-button>
            </el-button-group>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="editDialog.visible = false">取消</el-button>
          <el-button type="primary" :loading="editDialog.saving" @click="saveEditSchedule">保存</el-button>
        </template>
      </el-dialog>

      <!-- 触发历史 -->
      <div v-if="scheduleData.history?.length" class="history">
        <div class="history-title">最近触发记录</div>
        <div v-for="(h, i) in scheduleData.history" :key="i" class="history-item">
          <el-tag :type="h.status === 'started' ? 'success' : 'info'" size="small">
            {{ h.status === 'started' ? '已启动' : '已跳过' }}
          </el-tag>
          <span class="history-spider">{{ h.spider }}</span>
          <span class="history-time">{{ h.time }}</span>
          <span v-if="h.reason" class="history-reason">{{ h.reason }}</span>
        </div>
      </div>
    </el-card>

    <!-- 任务统计 -->
    <el-card shadow="never" class="block">
      <template #header>
        <b>任务统计</b>
        <el-button size="small" link @click="refreshStats" style="float: right">刷新</el-button>
      </template>
      <div class="stats-overview">
        <div class="stat-card">
          <div class="stat-num">{{ crawlStats.total || 0 }}</div>
          <div class="stat-label">总任务数</div>
        </div>
        <div class="stat-card success">
          <div class="stat-num">{{ crawlStats.completed || 0 }}</div>
          <div class="stat-label">成功</div>
        </div>
        <div class="stat-card danger">
          <div class="stat-num">{{ crawlStats.failed || 0 }}</div>
          <div class="stat-label">失败</div>
        </div>
        <div class="stat-card primary">
          <div class="stat-num">{{ crawlStats.success_rate || 0 }}%</div>
          <div class="stat-label">成功率</div>
        </div>
      </div>
      <!-- 按爬虫统计 -->
      <div v-if="crawlStats.by_spider?.length" class="stats-by-spider">
        <div class="stats-title">按爬虫统计</div>
        <div v-for="s in crawlStats.by_spider" :key="s.spider" class="spider-bar">
          <span class="spider-name">{{ spiderLabel(s.spider) }}</span>
          <div class="bar-wrap">
            <div class="bar success" :style="{ width: (s.completed / s.total * 100) + '%' }"></div>
            <div class="bar danger" :style="{ width: (s.failed / s.total * 100) + '%', left: (s.completed / s.total * 100) + '%' }"></div>
          </div>
          <span class="spider-count">{{ s.total }} 次</span>
        </div>
      </div>
    </el-card>

    <!-- 任务历史 -->
    <el-card shadow="never" class="block">
      <template #header><b>任务历史</b></template>
      <el-table :data="crawlHistory" size="small" v-if="crawlHistory.length">
        <el-table-column label="爬虫" width="140">
          <template #default="{ row }">{{ spiderLabel(row.spider) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'completed' ? 'success'
                              : row.status === 'failed' ? 'danger'
                              : row.status === 'empty' ? 'info' : 'warning'" size="small">
              {{ row.status === 'completed' ? '成功'
                 : row.status === 'failed' ? '失败'
                 : row.status === 'empty' ? '无数据' : '运行中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" prop="started_at" width="170" />
        <el-table-column label="结束时间" prop="ended_at" width="170">
          <template #default="{ row }">{{ row.ended_at || '-' }}</template>
        </el-table-column>
        <el-table-column label="PID" prop="pid" width="80" />
      </el-table>
      <el-empty v-else description="暂无任务历史" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  api, errText, type SpiderInfo, type CrawlStatusResult, type CrawlStatsResult,
  type CrawlHistoryItem, type ScheduleJob, type ScheduleListResult,
} from '../api'

// 初始值包含完整列表（与后端 SPIDERS 配置一致），后端离线时仍可正常展示
const spiders = ref<SpiderInfo[]>([
  { key: 'douyin_hot', label: '抖音热点榜' },
  { key: 'aihot_hot', label: 'AI热点榜(AIHOT)' },
  { key: 'aihot_news', label: 'AI资讯+日报(AIHOT)' },
  { key: 'news_rss', label: '新闻RSS(4源)' },
  { key: 'news_backfill', label: '新闻回填(中新网180天)' },
])
const spider = ref('douyin_hot')
const pages = ref(2)
const js = ref(false)
const starting = ref(false)
const status = ref<CrawlStatusResult>({ running: false, status: null, started_at: null, spider: null, log_tail: [] })
let timer: ReturnType<typeof setTimeout> | undefined

async function refresh(): Promise<void> {
  try { status.value = await api.crawlStatus() } catch { /* 后端离线时静默 */ }
  schedule()
}
function schedule(): void {
  clearTimeout(timer)
  timer = setTimeout(refresh, status.value.running ? 2000 : 8000)
}

async function start(): Promise<void> {
  starting.value = true
  try {
    const r = await api.crawlStart(spider.value, pages.value, js.value)
    if (r.started) ElMessage.success(`「${r.label || spider.value}」采集任务已启动`)
    else ElMessage.warning(r.reason || '启动失败')
    await refresh()
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    starting.value = false
  }
}

// ---- 定时任务 ----
const scheduleData = ref<ScheduleListResult>({ jobs: [], history: [], scheduler_active: true })
const newSchedule = ref({ spider: 'douyin_hot', cron: '0 8 * * *' })
const addingSchedule = ref(false)

async function refreshSchedule(): Promise<void> {
  try {
    scheduleData.value = await api.scheduleList()
  } catch { /* 后端离线时静默 */ }
}

async function addSchedule(): Promise<void> {
  if (!newSchedule.value.cron.trim()) {
    ElMessage.warning('请填写 cron 表达式')
    return
  }
  addingSchedule.value = true
  try {
    const r = await api.scheduleAdd(
      newSchedule.value.spider, newSchedule.value.cron,
      pages.value, js.value
    )
    if (r.ok) {
      ElMessage.success(r.scheduler_active ? '定时任务已添加' : '任务已保存（APScheduler 未安装，不会自动触发）')
      newSchedule.value.cron = ''
      await refreshSchedule()
    } else {
      ElMessage.warning(r.reason || '添加失败')
    }
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    addingSchedule.value = false
  }
}

async function removeSchedule(jobId: string): Promise<void> {
  try {
    await api.scheduleRemove(jobId)
    ElMessage.success('已删除')
    await refreshSchedule()
  } catch (e) {
    ElMessage.error(errText(e))
  }
}

// ---- 编辑定时任务 ----
const editDialog = ref({ visible: false, saving: false, jobId: '', spider: '', cron: '' })

function openEditSchedule(row: ScheduleJob): void {
  editDialog.value = {
    visible: true, saving: false,
    jobId: row.id, spider: row.spider, cron: row.cron || '',
  }
}

async function saveEditSchedule(): Promise<void> {
  if (!editDialog.value.cron.trim()) {
    ElMessage.warning('请填写 cron 表达式')
    return
  }
  editDialog.value.saving = true
  try {
    const r = await api.scheduleUpdate(
      editDialog.value.jobId, editDialog.value.cron.trim(), editDialog.value.spider)
    if (r.ok) {
      ElMessage.success('定时任务已更新')
      editDialog.value.visible = false
      await refreshSchedule()
    } else {
      ElMessage.warning(r.reason || '更新失败')
    }
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    editDialog.value.saving = false
  }
}

async function toggleSchedule(jobId: string, enabled: boolean | string | number): Promise<void> {
  try {
    await api.scheduleToggle(jobId, enabled)
    await refreshSchedule()
  } catch (e) {
    ElMessage.error(errText(e))
  }
}

// ---- 任务统计与历史 ----
const crawlStats = ref<CrawlStatsResult>({ total: 0, completed: 0, failed: 0, empty: 0, running: 0, success_rate: 0, by_day: [], by_spider: [] })
const crawlHistory = ref<CrawlHistoryItem[]>([])

function spiderLabel(key: string): string {
  const s = spiders.value.find(x => x.key === key)
  return s ? s.label : key
}

async function refreshStats(): Promise<void> {
  try {
    crawlStats.value = await api.crawlStats()
    crawlHistory.value = (await api.crawlHistory(30)).history || []
  } catch { /* 后端离线时静默 */ }
}

onMounted(async () => {
  refresh()
  refreshSchedule()
  refreshStats()
  try {
    const d = await api.crawlSpiders()
    if (d.spiders?.length) {
      spiders.value = d.spiders
      if (!d.spiders.find(s => s.key === spider.value)) {
        spider.value = d.spiders[0].key
      }
    }
  } catch { /* 用默认列表 */ }
})
onUnmounted(() => clearTimeout(timer))
</script>

<style scoped>
.block { margin-bottom: 16px; }
.tip { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.8; }
.tip code { background: var(--el-fill-color-light); padding: 1px 5px; border-radius: 4px; }
.tag { margin-left: 10px; }
.started { float: right; font-size: 12px; color: var(--el-text-color-secondary); }
.log {
  background: #0d1117; color: #8b949e; border-radius: 8px;
  padding: 14px; font-size: 12px; line-height: 1.7;
  max-height: 420px; overflow: auto; margin: 0;
  font-family: "SF Mono", Menlo, Consolas, monospace;
}
.schedule-form { margin-bottom: 8px; }
.hint { font-weight: 400; font-size: 12px; color: var(--el-text-color-secondary); margin-left: 8px; }
.dim { color: var(--el-text-color-secondary); }
.history { margin-top: 16px; border-top: 1px solid var(--el-border-color-lighter); padding-top: 12px; }
.history-title { font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 8px; }
.history-item { display: flex; align-items: center; gap: 10px; font-size: 12px; padding: 4px 0; }
.history-spider { font-weight: 600; }
.history-time { color: var(--el-text-color-secondary); }
.history-reason { color: var(--el-text-color-secondary); margin-left: auto; }
.stats-overview { display: flex; gap: 12px; margin-bottom: 16px; }
.stat-card {
  flex: 1; text-align: center; padding: 16px 8px; border-radius: 8px;
  background: var(--el-fill-color-light);
}
.stat-card.success { background: var(--el-color-success-light-9); }
.stat-card.danger { background: var(--el-color-danger-light-9); }
.stat-card.primary { background: var(--el-color-primary-light-9); }
.stat-num { font-size: 24px; font-weight: 700; color: var(--el-text-color-primary); }
.stat-label { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
.stats-by-spider { margin-top: 8px; }
.stats-title { font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); margin-bottom: 8px; }
.spider-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 12px; }
.spider-name { width: 120px; flex-shrink: 0; }
.bar-wrap { flex: 1; height: 16px; background: var(--el-fill-color); border-radius: 4px; position: relative; overflow: hidden; }
.bar { position: absolute; top: 0; height: 100%; }
.bar.success { background: var(--el-color-success); left: 0; }
.bar.danger { background: var(--el-color-danger); }
.spider-count { width: 60px; text-align: right; color: var(--el-text-color-secondary); }
</style>
