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
        <el-input v-model="form.bypass_url" placeholder="http://192.168.x.x:10000/xxx/cookies" style="max-width:420px;" />
        <div style="margin-top:4px;color:#999;font-size:12px;">
          普通 JS 盾站点开启 CF Bypass 时使用，从 NAS 的 CloudflareBypassForScraping 服务获取 cf_clearance。留空则禁用自动 bypass。
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
import { onMounted, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const form = reactive({ wecom_enabled: false, wecom_webhook: '', timezone: 'Asia/Shanghai', bypass_url: '' })

async function load() {
  const { data } = await api.getSettings()
  Object.assign(form, data)
}
async function save() {
  await api.updateSettings(form)
  ElMessage.success('已保存')
}

onMounted(load)
</script>
