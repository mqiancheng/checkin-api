<template>
  <el-container style="height: 100vh">
    <el-aside width="200px" style="background: #001529">
      <div style="color:#fff; font-size:18px; font-weight:600; padding:18px 16px;">
        签到助手
      </div>
      <el-menu background-color="#001529" text-color="#fff" active-text-color="#409EFF" router>
        <el-menu-item index="/"><el-icon><Histogram /></el-icon>仪表盘</el-menu-item>
        <el-menu-item index="/logs"><el-icon><Document /></el-icon>运行日志</el-menu-item>
        <el-menu-item index="/settings"><el-icon><Setting /></el-icon>全局设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header style="display:flex; align-items:center; border-bottom:1px solid #eee; background:#fff;">
        <span style="font-size:16px; font-weight:600;">{{ title }}</span>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Histogram, Document, Setting } from '@element-plus/icons-vue'

const route = useRoute()
const titles = {
  dashboard: '仪表盘',
  logs: '运行日志',
  settings: '全局设置',
}
const title = computed(() => titles[route.name] || '签到助手')
</script>
