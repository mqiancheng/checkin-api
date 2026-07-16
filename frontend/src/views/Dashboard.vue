<template>
  <div>
    <div style="display:flex; justify-content:space-between; margin-bottom:16px;">
      <el-button type="primary" :icon="Plus" @click="goNew">新建任务</el-button>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </div>

    <el-empty v-if="!tasks.length" description="还没有任务，点击右上角新建" />

    <el-row :gutter="16">
      <el-col v-for="t in tasks" :key="t.id" :xs="24" :sm="12" :md="8" style="margin-bottom:16px;">
        <el-card shadow="hover">
          <template #header>
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <span style="font-weight:600;">{{ t.name }}</span>
              <el-switch v-model="t.enabled" @change="(v) => toggle(t, v)" />
            </div>
          </template>
          <div style="font-size:13px; color:#666; word-break:break-all;">
            <div><b>方法:</b> {{ t.method }} <b>时间:</b> {{ scheduleText(t) }}</div>
            <div style="margin-top:4px; display:flex; gap:6px; flex-wrap:wrap;">
              <el-tag size="small" :type="t.executor_type === 'browser' ? 'warning' : (t.executor_type === 'chrome' ? '' : 'info')">
                {{ t.executor_type === 'browser' ? '浏览器' : (t.executor_type === 'chrome' ? 'Chrome' : 'HTTP') }}
              </el-tag>
              <el-tag v-if="t.executor_type !== 'browser' && t.executor_type !== 'chrome' && t.cf_bypass !== 'auto'" size="small" type="success">
                CF Bypass: {{ t.cf_bypass }}
              </el-tag>
            </div>
            <div style="margin-top:6px;"><b>URL:</b> {{ t.url }}</div>
          </div>
          <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;">
            <el-button size="small" :icon="VideoPlay" :loading="runningId === t.id" @click="run(t)">立即执行</el-button>
            <el-button size="small" :icon="Edit" @click="goEdit(t)">编辑</el-button>
            <el-button size="small" type="danger" :icon="Delete" @click="remove(t)">删除</el-button>
          </div>
          <el-alert
            v-if="lastLog[t.id]"
            :title="lastLog[t.id].success ? '上次成功' : '上次失败'"
            :type="lastLog[t.id].success ? 'success' : 'error'"
            :closable="false"
            style="margin-top:12px;"
          >
            <template #default>
              <pre style="white-space:pre-wrap; font-size:12px; margin:0;">{{ lastLog[t.id].formatted || lastLog[t.id].error }}</pre>
            </template>
          </el-alert>
        </el-card>
      </el-col>
    </el-row>

    <!-- 执行结果弹窗（实时日志） -->
    <el-dialog v-model="runDialogVisible" :title="`执行：${runTaskName}`" width="65%" :close-on-click-modal="false">
      <template #footer>
        <el-button @click="runDialogVisible = false">关闭</el-button>
      </template>

      <div v-if="runLoading" style="color:#909399;">
        <el-icon class="is-loading" style="margin-right:4px;"><Loading /></el-icon>正在执行...
      </div>
      <el-alert
        v-else
        :title="runResult?.success ? '执行成功' : '执行失败'"
        :type="runResult?.success ? 'success' : 'error'"
        :closable="false"
        style="margin-bottom:14px;"
      >
        <template #default>
          <pre style="white-space:pre-wrap; font-size:13px; margin:0;">{{ runLiveLog }}</pre>
        </template>
      </el-alert>

      <!-- 实时过程日志 -->
      <div style="background:#1e1e1e; color:#dcdcdc; padding:14px; border-radius:6px; overflow:auto; max-height:420px; font-size:13px;">
        <pre style="margin:0; white-space:pre-wrap;">{{ runLiveLog || '(等待开始...)' }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, Refresh, VideoPlay, Edit, Delete, Loading } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const router = useRouter()
const tasks = ref([])
const lastLog = ref({})
const runningId = ref(null)

// 执行弹窗状态
const runDialogVisible = ref(false)
const runTaskName = ref('')
const runLoading = ref(true)
const runResult = ref(null)
const runLiveLog = ref('')
let runPollTimer = null

function scheduleText(t) {
  if (t.schedule_type === 'cron') return t.cron_expr || '-'
  return `${String(t.hour).padStart(2, '0')}:${String(t.minute).padStart(2, '0')}` +
    (t.random_delay ? ` ±${t.random_delay}s` : '')
}

async function load() {
  const { data } = await api.listTasks()
  tasks.value = data
  for (const t of data) {
    const r = await api.listLogs(t.id, 1)
    if (r.data.length) lastLog.value[t.id] = r.data[0]
  }
}
function goNew() { router.push('/task/new') }
function goEdit(t) { router.push(`/task/${t.id}`) }

async function toggle(t, v) {
  await api.updateTask(t.id, { ...t, enabled: v })
}
async function remove(t) {
  await ElMessageBox.confirm(`确认删除任务「${t.name}」？`, '提示', { type: 'warning' })
  await api.deleteTask(t.id)
  load()
}

async function run(t) {
  runningId.value = t.id
  // 重置弹窗状态
  runTaskName.value = t.name
  runLoading.value = true
  runResult.value = null
  runLiveLog.value = '⏳ 正在执行，请稍候...'
  runDialogVisible.value = true

  try {
    const { data } = await api.runTask(t.id)
    // 后端已改为异步，立即返回 log_id → 开始轮询实时日志
    if (data.log_id) {
      startPolling(data.log_id)
    } else {
      // 异常：没有 log_id（不应该发生）
      runResult.value = { success: false }
      runLiveLog.value = data.error || '未获取到日志 ID'
      runLoading.value = false
    }
  } catch (e) {
    runResult.value = { success: false }
    runLiveLog.value = `请求失败: ${e.message || e}`
    runLoading.value = false
  } finally {
    runningId.value = null
    load()
  }
}

/* ---------- 轮询实时日志 ---------- */
function startPolling(logId) {
  pollOnce(logId)          // 立即拉一次
  runPollTimer = setInterval(() => pollOnce(logId), 1000)  // 每秒轮询
}

async function pollOnce(logId) {
  try {
    const { data } = await api.logDetail(logId)

    // 判断是否还在执行中：
    //   - status_code == 0 且 success == false 且 formatted 以 ⏳ 开头 → 还在跑
    const stillRunning = (
      (data.status_code === 0 || data.status_code == null) &&
      !data.success &&
      (data.formatted || '').startsWith('⏳')
    )

    if (stillRunning) {
      // 执行中：显示实时过程（formatted 此时包含 ⏳ + ▶ 步骤）
      runLiveLog.value = data.formatted || ''
    } else {
      // 执行完毕：保留过程步骤 + 追加最终结果
      stopPolling()
      runResult.value = { success: !!data.success }
      runLoading.value = false
      const steps = (data.process_log || '').trim()
      const result = data.formatted || data.error || (data.success ? '执行完成' : '执行失败')
      runLiveLog.value = steps ? (steps + '\n\n─── 执行结果 ───\n' + result) : result
    }
  } catch {
    // 日志可能还没落库，忽略
  }
}

function stopPolling() {
  if (runPollTimer) {
    clearInterval(runPollTimer)
    runPollTimer = null
  }
}

onBeforeUnmount(() => stopPolling())
onMounted(load)
</script>
