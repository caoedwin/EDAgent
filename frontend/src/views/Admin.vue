<template>
  <div class="admin-layout">
    <header class="admin-header">
      <div class="header-left">
        <img src="/favicon.svg" alt="logo" class="logo" />
        <h2>管理后台</h2>
      </div>
      <div class="header-right">
        <el-button :icon="ArrowLeft" @click="router.push('/')">返回聊天</el-button>
        <el-button :icon="Refresh" @click="loadUsers" :loading="loading">刷新</el-button>
      </div>
    </header>

    <main class="admin-main">
      <el-table :data="users" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column prop="username" label="用户名" min-width="150" />
        <el-table-column label="角色" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'warning' : 'info'" effect="light">
              {{ row.role === 'admin' ? '管理员' : '普通用户' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="conversations" label="会话数" width="100" align="center" />
        <el-table-column label="注册时间" width="180" align="center">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.role === 'user'"
              link
              type="warning"
              size="small"
              @click="changeRole(row, 'admin')"
            >提升为管理员</el-button>
            <el-button
              v-else
              link
              type="info"
              size="small"
              @click="changeRole(row, 'user')"
            >降为普通用户</el-button>
            <el-popconfirm
              :title="`确认删除用户「${row.username}」？其全部会话和消息将一并删除。`"
              @confirm="deleteUser(row)"
            >
              <template #reference>
                <el-button link type="danger" size="small" :disabled="row.id === auth.user?.id">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const users = ref([])
const loading = ref(false)

async function loadUsers() {
  loading.value = true
  try {
    const data = await api.get('/api/v1/admin/users')
    users.value = data.items
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function changeRole(row, role) {
  try {
    await api.patch(`/api/v1/admin/users/${row.id}/role`, { role })
    row.role = role
    ElMessage.success(`已将「${row.username}」${role === 'admin' ? '提升为管理员' : '降为普通用户'}`)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function deleteUser(row) {
  try {
    await api.delete(`/api/v1/admin/users/${row.id}`)
    users.value = users.value.filter((u) => u.id !== row.id)
    ElMessage.success(`用户「${row.username}」已删除`)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function formatTime(ts) {
  if (!ts) return '-'
  return String(ts).slice(0, 19).replace('T', ' ')
}

onMounted(loadUsers)
</script>

<style scoped>
.admin-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  background: var(--ea-panel);
  border-bottom: 1px solid var(--ea-border);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.logo {
  width: 28px;
  height: 28px;
}
.header-left h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.header-right {
  display: flex;
  gap: 10px;
}
.admin-main {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}
</style>
