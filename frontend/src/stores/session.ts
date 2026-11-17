import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    role: '值班管理员',
    shiftLabel: '白班 08:00-20:00',
    scope: '智能爬虫采集平台',
  }),
  getters: {
    // 只有值班管理员能核对与操作完整度报告，权限不足的账户只能查看
    canOperate: (state) => state.role === '值班管理员',
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setRole(role: string) {
      this.role = role
      this.operator = role === '值班管理员' ? '值班管理员' : '访客账户'
    },
  },
})
