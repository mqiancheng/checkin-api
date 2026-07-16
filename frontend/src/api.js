import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

export default {
  parse(raw) {
    return http.post('/parse', { raw })
  },
  listTasks() {
    return http.get('/tasks')
  },
  getTask(id) {
    return http.get(`/tasks/${id}`)
  },
  createTask(payload) {
    return http.post('/tasks', payload)
  },
  updateTask(id, payload) {
    return http.put(`/tasks/${id}`, payload)
  },
  deleteTask(id) {
    return http.delete(`/tasks/${id}`)
  },
  runTask(id) {
    return http.post(`/tasks/${id}/run`)
  },
  listLogs(taskId = 0, limit = 50) {
    return http.get('/logs', { params: { task_id: taskId, limit } })
  },
  logDetail(id) {
    return http.get(`/logs/${id}`)
  },
  deleteLog(id) {
    return http.delete(`/logs/${id}`)
  },
  deleteLogs(taskId = 0) {
    return http.delete('/logs', { params: { task_id: taskId } })
  },
  getSettings() {
    return http.get('/settings')
  },
  updateSettings(payload) {
    return http.put('/settings', payload)
  },
}
