import { defineStore } from 'pinia'
import { api } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    ready: false, // 首次 /auth/me 是否完成
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
