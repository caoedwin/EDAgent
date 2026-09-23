<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="brand">
        <img src="/favicon.svg" alt="logo" class="brand-logo" />
        <div>
          <h1>EdAgent</h1>
          <p>本地 AI 助手 · 注册账号</p>
        </div>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent>
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            size="large"
            placeholder="3-32 位字母 / 数字 / 下划线 / 中文"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            size="large"
            placeholder="至少 6 位"
            show-password
            :prefix-icon="Lock"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            size="large"
            placeholder="再次输入密码"
            show-password
            :prefix-icon="Lock"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          class="submit-btn"
          :loading="loading"
          @click="submit"
        >
          注册并进入
        </el-button>
      </el-form>

      <p class="switch-tip">
        已有账号？<router-link to="/login">返回登录</router-link>
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const formRef = ref()
const loading = ref(false)
const form = ref({ username: '', password: '', confirm: '' })

const validateConfirm = (_rule, value, callback) => {
  if (value !== form.value.password) callback(new Error('两次输入的密码不一致'))
  else callback()
}

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '长度需为 3-32 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirm, trigger: 'blur' },
  ],
}

async function submit() {
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      await auth.register(form.value.username.trim(), form.value.password)
      ElMessage.success('注册成功，已自动登录')
      router.push('/')
    } catch (err) {
      ElMessage.error(err.message)
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped>
.auth-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1d2b53 0%, #3b6ef5 100%);
}
.auth-card {
  width: 380px;
  background: var(--ea-panel);
  border-radius: 16px;
  padding: 36px 32px 28px;
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.35);
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}
.brand-logo {
  width: 44px;
  height: 44px;
}
.brand h1 {
  margin: 0;
  font-size: 22px;
}
.brand p {
  margin: 2px 0 0;
  color: var(--ea-text-soft);
  font-size: 13px;
}
.submit-btn {
  width: 100%;
  margin-top: 4px;
}
.switch-tip {
  text-align: center;
  margin: 18px 0 0;
  font-size: 13px;
  color: var(--ea-text-soft);
}
.switch-tip a {
  color: var(--ea-primary);
  text-decoration: none;
  font-weight: 500;
}
</style>
