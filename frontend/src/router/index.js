import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue'),
      meta: { guestOnly: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('../views/Register.vue'),
      meta: { guestOnly: true },
    },
    {
      path: '/',
      name: 'chat',
      component: () => import('../views/Chat.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../views/Admin.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.ready) await auth.fetchMe()

  if (to.meta.requiresAuth && !auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && auth.user?.role !== 'admin') {
    return { name: 'chat' }
  }
  if (to.meta.guestOnly && auth.isLoggedIn) {
    return { name: 'chat' }
  }
})

// 任意请求 401 → 清理状态并跳登录
window.addEventListener('ea:unauthorized', () => {
  const auth = useAuthStore()
  auth.user = null
  if (router.currentRoute.value.name !== 'login') {
    router.push({ name: 'login' })
  }
})

export default router
