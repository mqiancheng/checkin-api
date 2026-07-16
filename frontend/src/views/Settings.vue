<template>
  <el-card style="max-width:640px;">
    <template #header>全局设置</template>
    <el-form label-width="140px">
      <el-form-item label="时区">
        <el-select v-model="form.timezone" style="width:240px;">
          <el-option label="Asia/Shanghai (北京)" value="Asia/Shanghai" />
          <el-option label="UTC" value="UTC" />
        </el-select>
      </el-form-item>
      <el-divider>Cloudflare Bypass 服务</el-divider>
      <el-form-item label="Bypass 地址">
        <el-input :value="bypassBase" readonly style="max-width:420px;" />
        <div style="margin-top:6px;color:#999;font-size:12px;line-height:1.7;">
          本项目已内置 CFBypass 端点（端口 <code>{{ cfbPort }}</code>），地址由部署自动识别，无需手动填写。<br />
          <b>外部调用（如青龙脚本）</b>：使用
          <code>http://{{ hostname }}:{{ cfbPort }}/&lt;你的CFB密码&gt;/cookies</code><br />
          其中「你的CFB密码」= 容器环境变量 <code>PASSWORD</code>（即 .env 中的 <code>CFB_PASSWORD</code>）；<br />
          青龙侧用同名变量 <code>BYPASS_PASSWORD</code> 设置成相同值即可，例如 <code>BYPASS_PASSWORD=你的强密码</code>。
        </div>
      </el-form-item>
      <el-divider>企业微信机器人通知</el-divider>
      <el-form-item label="启用通知">
        <el-switch v-model="form.wecom_enabled" />
      </el-form-item>
      <el-form-item label="Webhook 地址">
        <el-input v-model="form.wecom_webhook" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." style="max-width:420px;" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="save">保存设置</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const form = reactive({ wecom_enabled: false, wecom_webhook: '', timezone: 'Asia/Shanghai', bypass_url: '' })
const cfbPort = ref(10000)
const hostname = ref('')
const bypassBase = ref('')

async function load() {
  const { data } = await api.getSettings()
  Object.assign(form, data)
  cfbPort.value = data.cfb_port || 10000
  // 自动识别：用当前访问 WebUI 的主机名 + 内置 cfbypass 端口拼出外部调用地址
  hostname.value = window.location.hostname
  bypassBase.value = `${window.location.protocol}//${hostname.value}:${cfbPort.value}`
}
async function save() {
  await api.updateSettings(form)
  ElMessage.success('已保存')
}

onMounted(load)
</script>
