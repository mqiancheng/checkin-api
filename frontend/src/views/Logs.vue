<template>
  <div>
    <div style="display:flex; gap:12px; margin-bottom:16px; align-items:center;">
      <el-select v-model="filterTask" placeholder="全部任务" clearable style="width:240px;" @change="load">
        <el-option v-for="t in tasks" :key="t.id" :label="t.name" :value="t.id" />
      </el-select>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </div>

    <el-table :data="logs" stripe style="width:100%">
      <el-table-column prop="task_name" label="任务" />
      <el-table-column label="结果" width="100">
        <template #default="{ row }">
          <el-tag :type="row.success ? 'success' : 'danger'">{{ row.success ? '成功' : '失败' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status_code" label="HTTP" width="90" />
      <el-table-column label="摘要" min-width="260">
        <template #default="{ row }">
          <pre style="white-space:pre-wrap; margin:0; font-size:12px;">{{ row.formatted || row.error }}</pre>
        </template>
      </el-table-column>
      <el-table-column prop="ran_at" label="时间" width="200" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button size="small" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" title="执行详情（完整返回）" width="70%">
      <el-alert v-if="detail" :type="detail.success ? 'success' : 'error'" :closable="false" style="margin-bottom:12px;">
        <pre style="white-space:pre-wrap; margin:0;">{{ detail.formatted || detail.error }}</pre>
      </el-alert>
      <div style="font-weight:600; margin-bottom:6px;">完整 JSON 响应：</div>
      <pre style="background:#1e1e1e; color:#dcdcdc; padding:14px; border-radius:6px; overflow:auto; max-height:420px;">{{ prettyRaw }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import api from '../api'

const logs = ref([])
const tasks = ref([])
const filterTask = ref('')
const detailVisible = ref(false)
const detail = ref(null)
const prettyRaw = ref('')

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

onMounted(async () => {
  const { data } = await api.listTasks()
  tasks.value = data
  load()
})
</script>
