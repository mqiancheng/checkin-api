import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import TaskEdit from './views/TaskEdit.vue'
import Logs from './views/Logs.vue'
import Settings from './views/Settings.vue'

const routes = [
  { path: '/', name: 'dashboard', component: Dashboard },
  { path: '/task/new', name: 'task-new', component: TaskEdit },
  { path: '/task/:id', name: 'task-edit', component: TaskEdit, props: true },
  { path: '/logs', name: 'logs', component: Logs },
  { path: '/settings', name: 'settings', component: Settings },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
