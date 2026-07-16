<template>
  <div>
    <div v-for="(row, i) in innerRows" :key="i" style="display:flex; gap:8px; margin-bottom:8px; align-items:center;">
      <span style="width:28px; text-align:right; color:#999; font-size:13px; flex-shrink:0;">{{ i + 1 }}</span>
      <el-input v-model="row.key" placeholder="键（如 authorization）" style="flex:1;" />
      <el-input v-model="row.value" placeholder="值" style="flex:2;" />
      <el-button type="danger" :icon="Delete" circle @click="remove(i)" />
    </div>
    <el-button :icon="Plus" @click="add">添加一行</el-button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Delete, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
})

const innerRows = computed(() => props.modelValue)

function add() {
  props.modelValue.push({ key: '', value: '' })
}
function remove(i) {
  props.modelValue.splice(i, 1)
}
</script>
