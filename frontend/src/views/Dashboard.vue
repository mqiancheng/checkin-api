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
              <el-tag size="small" :type="t.executor_type === 'browser' ? 'warning' : 'info'">
                {{ t.executor_type === 'browser' ? '浏览器执行' : 'HTTP' }}
              </el-tag>
              <el-tag v-if="t.executor_type !== 'browser' && t.cf_bypass !== 'auto'" size="small" type="success">
                CF Bypass: {{ t.cf_bypass }}
              </el-tag>
            </div>
            <div style="margin-top:6px;"><b>URL:</b> {{ t.url }}</div>
          </div>
          <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;">
            <el-button size="small" :icon="VideoPlay" @click="run(t)">立即执行</el-button>
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
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, Refresh, VideoPlay, Edit, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const router = useRouter()
const tasks = ref([])
const lastLog = ref({})

function scheduleText(t) {
  if (t.schedule_type === 'cron') return t.cron_expr || '-'
  return `${String(t.hour).padStart(2, '0')}:${String(t.minute).padStart(2, '0')}` +
    (t.random_delay ? ` ±${t.random_delay}s` : '')
}

async function load() {
  const { data } = await api.listTasks()
  tasks.value = data
  // 拉取每个任务的最近一条日志
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
async function run(t) {
  const { data } = await api.runTask(t.id)
  ElMessage.success(data.success ? '执行成功' : '执行失败，请查看日志')
  load()
}
async function remove(t) {
  await ElMessageBox.confirm(`确认删除任务「${t.name}」？`, '提示', { type: 'warning' })
  await api.deleteTask(t.id)
  load()
}

onMounted(load)
</script>
