import { defineStore } from 'pinia'
import { api } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    ready: false, // 首次 /auth/me 是否完成
    allowRegister: true, // 是否开放注册（由公开端点 /auth/config 决定）
  }),
  getters: {
    isLoggedIn: (state) => !!state.user,
    username: (state) => state.user?.username || '',
  },
  actions: {
    async fetchMe() {
      try {
        this.user = await api.get('/api/v1/auth/me')
      } catch {
        this.user = null
      } finally {
        this.ready = true
      }
    },
    async fetchAuthConfig() {
      try {
        const cfg = await api.get('/api/v1/auth/config')
        this.allowRegister = !!cfg.allow_register
      } catch {
        // 取不到时保持宽松：仍展示入口，后端会以 403 兜底
        this.allowRegister = true
      }
    },
    async login(username, password) {
      this.user = await api.post('/api/v1/auth/login', { username, password })
    },
    async register(username, password) {
      this.user = await api.post('/api/v1/auth/register', { username, password })
    },
    async logout() {
      try {
        await api.post('/api/v1/auth/logout', {})
      } finally {
        this.user = null
      }
    },
  },
})
