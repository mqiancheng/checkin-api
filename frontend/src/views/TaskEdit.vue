<template>
  <div>
    <el-page-header @back="() => router.push('/')" content="任务编辑" style="margin-bottom:16px;" />

    <el-tabs v-model="tab">
      <el-tab-pane label="请求 (RAW 解析)" name="raw">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px;">
          粘贴从 Fiddler / 浏览器开发者工具 复制的原始请求，点击「解析」自动拆分；也可直接手写下方各字段。
        </el-alert>
        <el-input v-model="raw" type="textarea" :rows="12"
          placeholder="POST https://... HTTP/2&#10;Host: ...&#10;authorization: Bearer ...&#10;cookie: a=1; b=2&#10;&#10;{}" />
        <div style="margin:12px 0;">
          <el-button type="primary" @click="doParse">解析 RAW</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane label="请求参数" name="req">
        <el-form label-width="120px">
          <el-form-item label="任务名称"><el-input v-model="form.name" /></el-form-item>
          <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
          <el-form-item label="方法">
            <el-select v-model="form.method" style="width:160px;">
              <el-option v-for="m in methods" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <el-form-item label="URL"><el-input v-model="form.url" /></el-form-item>
          <el-form-item label="执行方式">
            <el-select v-model="form.executor_type" style="width:240px;">
              <el-option label="HTTP 请求（默认）" value="http" />
              <el-option label="浏览器内执行（反检测，应对 CF Managed 验证）" value="browser" />
            </el-select>
            <span style="margin-left:8px;color:#999;">vikacg 等需过人机验证的站点选「浏览器内执行」</span>
          </el-form-item>
          <el-form-item label="CF Bypass">
            <el-select v-model="form.cf_bypass" style="width:240px;">
              <el-option label="自动（被拦才调用，最省资源）" value="auto" />
              <el-option label="强制开启" value="on" />
              <el-option label="关闭" value="off" />
            </el-select>
            <span style="margin-left:8px;color:#999;">普通 JS 盾站点用，需先在「全局设置」填 NAS bypass 地址</span>
          </el-form-item>
          <el-form-item label="Headers"><KeyValueEditor :model-value="headerRows" /></el-form-item>
          <el-form-item label="Cookies"><KeyValueEditor :model-value="cookieRows" /></el-form-item>
          <el-form-item label="Query 参数"><KeyValueEditor :model-value="paramRows" /></el-form-item>
          <el-form-item label="Body 类型">
            <el-select v-model="form.body_type" style="width:160px;">
              <el-option label="JSON" value="json" />
              <el-option label="表单" value="form" />
              <el-option label="原始" value="raw" />
              <el-option label="无" value="none" />
            </el-select>
          </el-form-item>
          <el-form-item label="Body" v-if="form.body_type !== 'none'">
            <el-input v-model="form.body" type="textarea" :rows="6" />
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="定时" name="sched">
        <el-form label-width="140px">
          <el-form-item label="调度方式">
            <el-radio-group v-model="form.schedule_type">
              <el-radio value="daily">每天固定时间</el-radio>
              <el-radio value="cron">Cron 表达式</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="执行时间" v-if="form.schedule_type === 'daily'">
            <el-time-picker v-model="timeVal" format="HH:mm" value-format="HH:mm" @change="applyTime" />
          </el-form-item>
          <el-form-item label="Cron" v-else>
            <el-input v-model="form.cron_expr" placeholder="分 时 日 月 周，如 0 9 * * *" style="max-width:320px;" />
          </el-form-item>
          <el-form-item label="随机延时(秒)">
            <el-input-number v-model="form.random_delay" :min="0" :max="3600" />
            <span style="margin-left:8px;color:#999;">执行前随机等待 0~N 秒，避免同一秒打爆</span>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <el-tab-pane label="成功判定" name="cond">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px;">
          设置判断「执行成功」的条件，可任意增删。顶层逻辑支持 AND(全部满足)/OR(任一满足)；单条件也能用。
        </el-alert>
        <el-form label-width="100px">
          <el-form-item label="响应类型">
            <el-radio-group v-model="form.response_type">
              <el-radio value="json">JSON</el-radio>
              <el-radio value="text">纯文本</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="组合逻辑">
            <el-radio-group v-model="form.logic">
              <el-radio value="AND">AND（全部满足）</el-radio>
              <el-radio value="OR">OR（任一满足）</el-radio>
            </el-radio-group>
          </el-form-item>
        </el-form>
        <div v-for="(c, i) in form.conditions" :key="i"
             style="display:flex; gap:8px; margin-bottom:8px; align-items:center;">
          <el-input v-model="c.path" placeholder="路径 如 status / data.code"
                    style="width:220px;" :disabled="form.response_type === 'text'" />
          <el-select v-model="c.op" style="width:130px;">
            <el-option v-for="o in ops" :key="o.v" :label="o.l" :value="o.v" />
          </el-select>
          <el-select v-model="c.value_type" style="width:110px;">
            <el-option label="自动" value="auto" />
            <el-option label="字符串" value="str" />
            <el-option label="数字" value="num" />
            <el-option label="布尔" value="bool" />
          </el-select>
          <el-input v-model="c.value" placeholder="期望值" style="width:160px;" :disabled="c.op === 'exists'" />
          <el-button type="danger" :icon="Delete" circle @click="form.conditions.splice(i, 1)" />
        </div>
        <el-button :icon="Plus" @click="addCond">添加条件</el-button>
      </el-tab-pane>

      <el-tab-pane label="日志展示" name="field">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px;">
          定义执行后想看到的返回字段。路径用点号进入嵌套，如 data.sign_count。无论成功失败，完整 JSON 都可在日志详情查看。
        </el-alert>
        <div v-for="(f, i) in form.fields" :key="i"
             style="display:flex; gap:8px; margin-bottom:8px; align-items:center;">
          <el-input v-model="f.path" placeholder="返回路径 如 message" style="width:240px;" />
          <el-input v-model="f.label" placeholder="显示名 如 消息" style="width:180px;" />
          <el-button type="danger" :icon="Delete" circle @click="form.fields.splice(i, 1)" />
        </div>
        <el-button :icon="Plus" @click="addField">添加字段</el-button>
      </el-tab-pane>
    </el-tabs>

    <div style="margin-top:20px; text-align:right;">
      <el-button @click="router.push('/')">取消</el-button>
      <el-button type="primary" @click="save">保存</el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus, Delete } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import KeyValueEditor from '../components/KeyValueEditor.vue'

const route = useRoute()
const router = useRouter()
const raw = ref('')
const tab = ref('raw')
const timeVal = ref('09:00')

const methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']
const ops = [
  { l: '等于', v: 'eq' }, { l: '不等于', v: 'ne' }, { l: '包含', v: 'contains' },
  { l: '存在', v: 'exists' }, { l: '大于', v: 'gt' }, { l: '大于等于', v: 'ge' },
  { l: '小于', v: 'lt' }, { l: '小于等于', v: 'le' }, { l: '在列表中', v: 'in' },
]

const form = reactive({
  name: '未命名任务', enabled: true, method: 'POST', url: '',
  headers: {}, cookies: {}, params: {}, body: '', body_type: 'json', raw_text: '',
  executor_type: 'http', cf_bypass: 'auto',
  schedule_type: 'daily', hour: 9, minute: 0, cron_expr: '', random_delay: 0,
  logic: 'AND', conditions: [], fields: [], response_type: 'json',
})
const headerRows = ref([])
const cookieRows = ref([])
const paramRows = ref([])

function dictToRows(d) {
  return Object.entries(d || {}).map(([k, v]) => ({ key: k, value: String(v) }))
}
function rowsToDict(rows) {
  const o = {}
  for (const r of rows) if (r.key) o[r.key] = r.value
  return o
}
function applyTime() {
  const [h, m] = (timeVal.value || '09:00').split(':')
  form.hour = Number(h)
  form.minute = Number(m)
}

async function loadTask(id) {
  const { data } = await api.getTask(id)
  Object.assign(form, data)
  headerRows.value = dictToRows(data.headers)
  cookieRows.value = dictToRows(data.cookies)
  paramRows.value = dictToRows(data.params)
  timeVal.value = `${String(data.hour).padStart(2, '0')}:${String(data.minute).padStart(2, '0')}`
}

async function doParse() {
  if (!raw.value.trim()) { ElMessage.warning('请先粘贴 RAW'); return }
  const { data } = await api.parse(raw.value)
  form.method = data.method
  form.url = data.url
  form.body = data.body
  form.body_type = data.body_type
  headerRows.value = dictToRows(data.headers)
  cookieRows.value = dictToRows(data.cookies)
  paramRows.value = dictToRows(data.params)
  form.raw_text = raw.value
  ElMessage.success('解析完成，请检查下方字段')
}

function addCond() { form.conditions.push({ path: '', op: 'eq', value: '', value_type: 'auto' }) }
function addField() { form.fields.push({ path: '', label: '' }) }

async function save() {
  applyTime()
  const payload = {
    ...form,
    headers: rowsToDict(headerRows.value),
    cookies: rowsToDict(cookieRows.value),
    params: rowsToDict(paramRows.value),
  }
  if (route.params.id) {
    await api.updateTask(route.params.id, payload)
    ElMessage.success('已更新')
  } else {
    await api.createTask(payload)
    ElMessage.success('已创建')
  }
  router.push('/')
}

onMounted(() => { if (route.params.id) loadTask(route.params.id) })
</script>
