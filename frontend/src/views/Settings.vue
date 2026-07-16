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
      <el-form-item label="验证方式说明">
        <div style="color:#888;font-size:12px;line-height:1.9;">
          <p style="margin:0 0 6px;"><b>① 交互式验证（过 Turnstile 控件）</b></p>
          适用于需要<b>登录账号、提交表单、点击按钮</b>才能完成的验证（如青龙脚本登录场景）。<br />
          配置：任务 → 执行方式选 <code>浏览器内执行</code>，CloakBrowser 会在页面内自动点击 Turnstile 复选框并执行请求，无需手动处理 cf_clearance。<br />
          外部调用（青龙）：<code>POST /&lt;密码&gt;/exec</code>
          <p style="margin:10px 0 6px;"><b>② 获取 CF Cookies（cf_clearance）</b></p>
          适用于<b>只需打开网站拿到 clearance 即可发送 API 请求</b>的场景（最常见）。<br />
          配置：任务 → 执行方式保持 <code>HTTP</code>，CF Bypass 设为 <code>auto</code>（默认，被拦才调）或 <code>on</code>（强制每次刷新），系统自动获取并注入 cf_clearance。<br />
          外部调用（青龙）：<code>GET /&lt;密码&gt;/cookies</code> 或 <code>/&lt;密码&gt;/turnstile</code>
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
