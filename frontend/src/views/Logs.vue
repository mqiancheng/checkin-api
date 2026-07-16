<template>
  <div>
    <div style="display:flex; gap:12px; margin-bottom:16px; align-items:center;">
      <el-select v-model="filterTask" placeholder="全部任务" clearable style="width:240px;" @change="load">
        <el-option v-for="t in tasks" :key="t.id" :label="t.name" :value="t.id" />
      </el-select>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
      <el-popconfirm title="确认清空所有日志？" @confirm="deleteAll">
        <template #reference>
          <el-button type="danger" plain :icon="Delete">清空日志</el-button>
        </template>
      </el-popconfirm>
    </div>

    <el-table :data="logs" stripe style="width:100%">
      <el-table-column prop="task_name" label="任务" />
      <el-table-column label="结果" width="100">
        <template #default="{ row }">
          <el-tag :type="row.success ? 'success' : 'danger'" size="small">{{ row.success ? '成功' : '失败' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="HTTP" width="90">
        <template #default="{ row }">
          {{ row.status_code ? row.status_code : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="摘要" min-width="260">
        <template #default="{ row }">
          <pre style="white-space:pre-wrap; margin:0; font-size:12px;">{{ row.formatted || row.error }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="开始时间" width="170">
        <template #default="{ row }">{{ fmtTime(row.ran_at) }}</template>
      </el-table-column>
      <el-table-column label="完成时间" width="170">
        <template #default="{ row }">{{ row.finished_at ? fmtTime(row.finished_at) : '执行中…' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="140">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">详情</el-button>
          <el-popconfirm title="确认删除此条日志？" @confirm="deleteOne(row)">
            <template #reference>
              <el-button size="small" type="danger" plain>删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" title="执行详情" width="70%">
      <!-- 执行过程 + 结果 -->
      <div style="background:#1e1e1e; color:#dcdcdc; padding:14px; border-radius:6px; overflow:auto; max-height:400px; margin-bottom:12px;">
        <pre style="margin:0; white-space:pre-wrap; font-size:13px;">{{ detailText }}</pre>
      </div>
      <div style="font-weight:600; margin-bottom:6px;">完整 JSON 响应：</div>
      <pre style="background:#1e1e1e; color:#dcdcdc; padding:14px; border-radius:6px; overflow:auto; max-height:420px;">{{ prettyRaw }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref, computed } from 'vue'
import { Refresh, Delete } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const logs = ref([])
const tasks = ref([])
const filterTask = ref('')
const detailVisible = ref(false)
const detail = ref(null)
const prettyRaw = ref('')

// 详情弹窗内容：过程步骤 + 最终结果
const detailText = computed(() => {
  if (!detail.value) return ''
  const steps = (detail.value.process_log || '').trim()
  const result = detail.value.formatted || detail.value.error || ''
  if (steps && result) return steps + '\n\n─── 执行结果 ───\n' + result
  return steps || result
})

function fmtTime(s) {
  if (!s) return ''
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function load() {
  const { data } = await api.listLogs(filterTask.value || 0, 100)
  logs.value = data
}
async function openDetail(row) {
  const { data } = await api.logDetail(row.id)
  detail.value = data
  try {
    prettyRaw.value = JSON.stringify(JSON.parse(data.raw_response), null, 2)
  } catch {
    prettyRaw.value = data.raw_response
  }
  detailVisible.value = true
}

async function deleteOne(row) {
  await api.deleteLog(row.id)
  ElMessage.success('已删除')
  load()
}
async function deleteAll() {
  const taskId = filterTask.value || 0
  const { data } = await api.deleteLogs(taskId)
  ElMessage.success(`已删除 ${data.deleted} 条日志`)
  load()
}

onMounted(async () => {
  const { data } = await api.listTasks()
  tasks.value = data
  load()
})
</script>
