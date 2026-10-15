<template>
  <div class="team-layout">
    <header class="team-header">
      <div class="header-left">
        <img src="/favicon.svg" alt="logo" class="logo" />
        <h2>团队协作</h2>
      </div>
      <div class="header-right">
        <!-- 通知铃铛 -->
        <el-popover placement="bottom-end" :width="360" trigger="click" @show="loadNotifications">
          <template #reference>
            <el-badge :value="unreadCount" :hidden="!unreadCount" class="bell">
              <el-button :icon="Bell" circle />
            </el-badge>
          </template>
          <div class="notif-panel">
            <div class="notif-head">
              <span>通知</span>
              <el-button link type="primary" size="small" :disabled="!unreadCount" @click="markAllRead">
                全部已读
              </el-button>
            </div>
            <el-empty v-if="!notifications.length" description="暂无通知" :image-size="60" />
            <div
              v-for="n in notifications"
              :key="n.id"
              class="notif-item"
              :class="{ unread: !n.is_read }"
            >
              <div class="notif-title">{{ n.title }}</div>
              <div v-if="n.body" class="notif-body">{{ n.body }}</div>
              <div class="notif-foot">
                <span class="notif-time">{{ formatTime(n.created_at) }}</span>
                <span class="notif-actions">
                  <el-button v-if="n.team_id" link type="primary" size="small" @click="openTeamFromNotification(n)">
                    查看
                  </el-button>
                  <el-button v-if="!n.is_read" link size="small" @click="markRead(n)">标记已读</el-button>
                </span>
              </div>
            </div>
          </div>
        </el-popover>
        <el-button :icon="ChatDotRound" @click="router.push('/')">返回聊天</el-button>
      </div>
    </header>

    <div class="team-body">
      <!-- 我的团队 -->
      <aside class="team-sidebar">
        <div class="sidebar-head">
          <span>我的团队</span>
          <span class="sidebar-actions">
            <el-button link :icon="Plus" @click="newTeam" />
            <el-button link :icon="Refresh" @click="loadTeams" />
          </span>
        </div>
        <el-empty v-if="!teams.length" description="暂无团队" :image-size="60" />
        <div
          v-for="t in teams"
          :key="t.id"
          class="team-item"
          :class="{ active: t.id === teamId }"
          @click="selectTeam(t)"
        >
          <div class="team-item-main">
            <span class="team-item-name">{{ t.name }}</span>
            <span class="team-item-meta">{{ t.task_count }} 任务 · {{ t.artifact_count }} 产物</span>
          </div>
          <div class="team-item-actions" @click.stop>
            <el-button link :icon="Edit" size="small" @click="renameTeam(t)" />
            <el-button link type="danger" :icon="Delete" size="small" @click="removeTeam(t)" />
          </div>
        </div>
      </aside>

      <main class="team-main">
        <!-- 目标输入区 -->
        <div class="goal-section">
          <div class="goal-toolbar">
            <el-select
              v-model="template"
              placeholder="选择团队模板（可选）"
              clearable
              :disabled="!!teamId"
              style="width: 240px"
            >
              <el-option v-for="t in templates" :key="t.key" :label="t.name" :value="t.key" />
            </el-select>
            <span v-if="currentTemplate" class="template-hint">{{ currentTemplate.goal_hint }}</span>
          </div>
          <el-input
            v-model="goal"
            type="textarea"
            :rows="3"
            :placeholder="currentTemplate?.goal_hint || '请描述你的目标，例如：帮我开发一个待办清单 API'"
            :disabled="!!teamId"
          />
          <el-button
            type="primary"
            :icon="VideoPlay"
            :loading="starting"
            :disabled="!goal.trim() || !!teamId"
            @click="startTeam"
          >
            {{ teamId ? '任务进行中' : '启动团队' }}
          </el-button>
        </div>

        <!-- 任务进度 -->
        <div v-if="teamId" class="progress-section">
          <div class="status-bar">
            <el-tag v-for="(count, status) in statusSummary" :key="status" size="small" :type="statusType(status)">
              {{ statusLabel(status) }}: {{ count }}
            </el-tag>
          </div>

          <div class="task-list">
            <div
              v-for="task in tasks"
              :key="task.id"
              class="task-card"
              :class="task.status"
            >
              <div class="task-head">
                <el-tag size="small" :type="roleColor(task.role)">{{ roleName(task.role) }}</el-tag>
                <span class="task-title">{{ task.title }}</span>
                <el-tag size="small" :type="statusType(task.status)" effect="plain">
                  {{ statusLabel(task.status) }}
                </el-tag>
              </div>
              <p v-if="task.description" class="task-desc">{{ task.description }}</p>

              <!-- 人工确认按钮 -->
              <div v-if="task.status === 'blocked'" class="confirm-bar">
                <span>等待人工确认：</span>
                <el-button type="success" size="small" :icon="Check" @click="confirm(task, true)">通过</el-button>
                <el-button type="danger" size="small" :icon="Close" @click="confirm(task, false)">拒绝</el-button>
              </div>

              <!-- 产物链接 -->
              <div v-if="task.artifact_id" class="artifact-bar">
                <el-button link type="primary" size="small" @click="showArtifact(task)">
                  <Document class="art-icon" /> 查看产物
                </el-button>
                <!-- PR 按钮（developer 角色） -->
                <el-button v-if="task.role === 'developer'" link type="warning" size="small" @click="showPrForTask(task)">
                  <Document class="art-icon" /> 查看代码变更 (PR)
                </el-button>
              </div>
            </div>
          </div>

          <el-button link :icon="Refresh" @click="loadProgress">刷新进度</el-button>
        </div>
      </main>
    </div>

    <!-- 产物弹窗 -->
    <el-dialog v-model="artifactVisible" :title="currentArtifact?.title" width="70%" top="5vh">
      <div class="artifact-meta">
        <el-tag size="small">{{ currentArtifact?.type }}</el-tag>
        <span class="ver">v{{ currentArtifact?.version }}</span>
      </div>
      <pre class="artifact-content">{{ currentArtifact?.content }}</pre>
    </el-dialog>

    <!-- PR 审查面板 -->
    <el-dialog v-model="prVisible" title="代码变更审查 (PR)" width="75%" top="5vh">
      <div v-if="currentPr">
        <div class="pr-meta">
          <el-tag size="small" :type="prStatusType(currentPr.status)">{{ prStatusLabel(currentPr.status) }}</el-tag>
          <span class="pr-title">{{ currentPr.title }}</span>
        </div>
        <div v-if="currentPr.files_changed?.length" class="pr-files">
          变更文件：{{ currentPr.files_changed.join(', ') }}
        </div>
        <pre class="diff-content">{{ currentPr.diff_content }}</pre>
        <div v-if="currentPr.reviewer_notes" class="pr-notes">
          评审意见：{{ currentPr.reviewer_notes }}
        </div>
        <div v-if="currentPr.status === 'pending' || currentPr.status === 'approved'" class="pr-actions">
          <el-button type="success" size="small" :icon="Check" @click="applyPr(currentPr)">应用补丁</el-button>
          <el-button type="danger" size="small" :icon="Close" @click="rejectPr(currentPr)">拒绝</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound, VideoPlay, Refresh, Check, Close, Document, Bell, Edit, Delete, Plus,
} from '@element-plus/icons-vue'
import { api } from '../api/client'

const router = useRouter()
const goal = ref('')
const teamId = ref('')
const tasks = ref([])
const statusSummary = ref({})
const starting = ref(false)
const artifactVisible = ref(false)
const currentArtifact = ref(null)
const prVisible = ref(false)
const currentPr = ref(null)
const prs = ref([])

// 团队模板 / 我的团队 / 通知
const templates = ref([])
const template = ref('')
const teams = ref([])
const notifications = ref([])
const unreadCount = ref(0)
const currentTemplate = computed(() => templates.value.find(t => t.key === template.value) || null)

let notifTimer = null

const prStatusLabels = { pending: '待审查', approved: '已通过', rejected: '已拒绝', applied: '已应用', needs_changes: '需修改' }
const prStatusTypes = { pending: 'warning', approved: 'success', rejected: 'danger', applied: 'success', needs_changes: 'warning' }

function prStatusLabel(s) { return prStatusLabels[s] || s }
function prStatusType(s) { return prStatusTypes[s] || 'info' }

const roleNames = {
  pm: '总控 PM',
  product: '产品',
  architect: '架构',
  developer: '开发',
  tester: '测试',
  reviewer: '评审',
}

const statusLabels = {
  pending: '等待中',
  running: '执行中',
  blocked: '待确认',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const statusTypes = {
  pending: 'info',
  running: 'warning',
  blocked: 'warning',
  done: 'success',
  failed: 'danger',
  cancelled: 'info',
}

function roleName(r) { return roleNames[r] || r }
function statusLabel(s) { return statusLabels[s] || s }
function statusType(s) { return statusTypes[s] || 'info' }
function roleColor(r) {
  return { pm: '', product: 'success', architect: 'warning', developer: 'primary', tester: 'info', reviewer: 'danger' }[r] || ''
}

function formatTime(s) {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d.getTime()) ? s : d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  loadTemplates()
  loadTeams()
  loadNotifications()
  notifTimer = setInterval(loadUnread, 15000)
})

onUnmounted(() => {
  if (notifTimer) clearInterval(notifTimer)
})

// ---------------- 团队模板 ----------------
async function loadTemplates() {
  try {
    const data = await api.get('/api/v1/team/templates')
    templates.value = data.items || []
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ---------------- 我的团队 ----------------
async function loadTeams() {
  try {
    const data = await api.get('/api/v1/team/list')
    teams.value = data.items || []
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function resetProgress() {
  tasks.value = []
  statusSummary.value = {}
}

function selectTeam(t) {
  teamId.value = t.id
  goal.value = ''
  resetProgress()
  loadProgress()
  loadPrs()
}

function newTeam() {
  teamId.value = ''
  goal.value = ''
  template.value = ''
  resetProgress()
}

async function renameTeam(t) {
  try {
    const { value } = await ElMessageBox.prompt('请输入新的团队名称', '重命名团队', {
      inputValue: t.name,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValidator: (v) => (v && v.trim() ? true : '名称不能为空'),
    })
    await api.patch(`/api/v1/team/${t.id}/rename`, { name: value.trim() })
    ElMessage.success('已重命名')
    await loadTeams()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') ElMessage.error(err?.message || String(err))
  }
}

async function removeTeam(t) {
  try {
    await ElMessageBox.confirm(`确定删除团队「${t.name}」？该操作会同时删除其任务与产物。`, '删除团队', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await api.delete(`/api/v1/team/${t.id}`)
    ElMessage.success('已删除')
    if (teamId.value === t.id) {
      teamId.value = ''
      resetProgress()
    }
    await loadTeams()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') ElMessage.error(err?.message || String(err))
  }
}

// ---------------- 通知 ----------------
async function loadNotifications() {
  try {
    const data = await api.get('/api/v1/team/notifications?limit=30')
    notifications.value = data.items || []
    unreadCount.value = notifications.value.filter(n => !n.is_read).length
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function loadUnread() {
  try {
    const data = await api.get('/api/v1/team/notifications/unread-count')
    unreadCount.value = data.count || 0
  } catch {
    /* 未读轮询静默失败 */
  }
}

async function markRead(n) {
  try {
    await api.post(`/api/v1/team/notifications/${n.id}/read`, {})
    n.is_read = true
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function markAllRead() {
  try {
    await api.post('/api/v1/team/notifications/read-all', {})
    notifications.value.forEach(n => { n.is_read = true })
    unreadCount.value = 0
    ElMessage.success('已全部标记为已读')
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function openTeamFromNotification(n) {
  if (!n.team_id) return
  teamId.value = n.team_id
  goal.value = ''
  resetProgress()
  loadProgress()
  loadPrs()
}

// ---------------- 启动 / 进度 ----------------
async function startTeam() {
  starting.value = true
  try {
    const payload = { goal: goal.value }
    if (template.value) payload.template = template.value
    const result = await api.post('/api/v1/team/start', payload)
    teamId.value = result.team_id
    ElMessage.success('团队任务已启动')
    pollProgress()
    await loadTeams()
    await loadNotifications()
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    starting.value = false
  }
}

async function loadProgress() {
  if (!teamId.value) return
  try {
    const data = await api.get(`/api/v1/team/${teamId.value}`)
    tasks.value = data.tasks
    statusSummary.value = data.status_summary
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function pollProgress() {
  loadProgress()
  loadPrs()
  const timer = setInterval(async () => {
    await loadProgress()
    await loadPrs()
    const allDone = tasks.value.every(t => ['done', 'failed', 'cancelled'].includes(t.status))
    if (allDone) {
      clearInterval(timer)
      loadUnread()
    }
  }, 3000)
}

async function confirm(task, approved) {
  try {
    await api.post(`/api/v1/team/${teamId.value}/tasks/${task.id}/confirm`, { approved })
    ElMessage.success(approved ? '已通过，继续执行' : '已拒绝')
    pollProgress()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function showArtifact(task) {
  try {
    const data = await api.get(`/api/v1/team/${teamId.value}/artifacts`)
    const art = data.items.find(a => a.task_id === task.id)
    if (art) {
      currentArtifact.value = art
      artifactVisible.value = true
    }
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// PR / 补丁
async function loadPrs() {
  if (!teamId.value) return
  try {
    const data = await api.get(`/api/v1/team/${teamId.value}/prs`)
    prs.value = data.items || []
  } catch { prs.value = [] }
}

function showPr(pr) {
  currentPr.value = pr
  prVisible.value = true
}

async function showPrForTask(task) {
  await loadPrs()
  const pr = prs.value.find(p => p.task_id === task.id)
  if (pr) {
    showPr(pr)
  } else {
    ElMessage.info('该任务暂无代码变更')
  }
}

async function applyPr(pr) {
  try {
    const result = await api.post(`/api/v1/team/${teamId.value}/prs/${pr.id}/apply`, {})
    ElMessage.success(`已应用补丁，修改文件：${result.applied_files?.join(', ')}`)
    await loadPrs()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function rejectPr(pr) {
  const reason = prompt('拒绝原因：') || ''
  try {
    await api.post(`/api/v1/team/${teamId.value}/prs/${pr.id}/reject`, { reason })
    ElMessage.success('已拒绝 PR')
    await loadPrs()
    prVisible.value = false
  } catch (err) {
    ElMessage.error(err.message)
  }
}
</script>

<style scoped>
.team-layout { display: flex; flex-direction: column; height: 100%; }
.team-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 24px; background: var(--ea-panel); border-bottom: 1px solid var(--ea-border);
}
.header-left { display: flex; align-items: center; gap: 10px; }
.header-right { display: flex; align-items: center; gap: 16px; }
.logo { width: 28px; height: 28px; }
.header-left h2 { margin: 0; font-size: 16px; font-weight: 600; }

.team-body { flex: 1; display: flex; min-height: 0; }

/* 侧栏：我的团队 */
.team-sidebar {
  width: 240px; flex-shrink: 0; border-right: 1px solid var(--ea-border);
  background: var(--ea-panel); overflow-y: auto; padding: 12px 10px;
}
.sidebar-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; font-weight: 600; color: var(--ea-text-2); padding: 4px 6px 10px;
}
.sidebar-actions { display: flex; gap: 2px; }
.team-item {
  display: flex; align-items: center; gap: 6px; padding: 8px 10px;
  border-radius: 6px; cursor: pointer; transition: background 0.2s;
}
.team-item:hover { background: var(--ea-bg); }
.team-item.active { background: var(--ea-bg); box-shadow: inset 2px 0 0 var(--el-color-primary); }
.team-item-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.team-item-name {
  font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.team-item-meta { font-size: 12px; color: var(--ea-text-2); }
.team-item-actions { display: flex; gap: 0; opacity: 0; transition: opacity 0.2s; }
.team-item:hover .team-item-actions { opacity: 1; }

/* 通知铃铛 */
.bell { margin-top: 4px; }
.notif-panel { max-height: 420px; overflow-y: auto; }
.notif-head {
  display: flex; align-items: center; justify-content: space-between;
  font-weight: 600; padding-bottom: 8px; border-bottom: 1px solid var(--ea-border); margin-bottom: 6px;
}
.notif-item {
  padding: 8px 6px; border-radius: 6px; border-bottom: 1px dashed var(--ea-border);
}
.notif-item:last-child { border-bottom: none; }
.notif-item.unread { background: var(--ea-bg); }
.notif-title { font-size: 13px; font-weight: 500; }
.notif-body { font-size: 12px; color: var(--ea-text-2); margin-top: 2px; }
.notif-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; }
.notif-time { font-size: 12px; color: var(--ea-text-2); }
.notif-actions { display: flex; gap: 8px; }

.team-main { flex: 1; padding: 24px; overflow-y: auto; max-width: 900px; margin: 0 auto; width: 100%; }
.goal-section { display: flex; flex-direction: column; gap: 12px; margin-bottom: 24px; }
.goal-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.template-hint { font-size: 12px; color: var(--ea-text-2); }
.status-bar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.task-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px; }
.task-card {
  background: var(--ea-panel); border: 1px solid var(--ea-border);
  border-radius: 8px; padding: 14px 16px; transition: border-color 0.2s;
}
.task-card.done { border-color: var(--ea-success); }
.task-card.failed { border-color: var(--ea-danger); }
.task-card.blocked { border-color: #e6a23c; }
.task-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.task-title { flex: 1; font-weight: 500; }
.task-desc { color: var(--ea-text-2); font-size: 13px; margin: 4px 0; }
.confirm-bar { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.artifact-bar { margin-top: 6px; }
.art-icon { vertical-align: middle; margin-right: 4px; }
.artifact-meta { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.ver { color: var(--ea-text-2); font-size: 13px; }
.artifact-content {
  background: var(--ea-bg); padding: 16px; border-radius: 8px;
  white-space: pre-wrap; word-break: break-word; max-height: 60vh; overflow-y: auto;
  font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.6;
}
.pr-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.pr-title { font-weight: 500; }
.pr-files { color: var(--ea-text-2); font-size: 13px; margin-bottom: 10px; }
.diff-content {
  background: var(--ea-bg); padding: 16px; border-radius: 8px;
  white-space: pre-wrap; word-break: break-word; max-height: 50vh; overflow-y: auto;
  font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; line-height: 1.5;
  border: 1px solid var(--ea-border);
}
.pr-notes { margin: 10px 0; padding: 8px 12px; background: var(--ea-panel); border-radius: 6px; font-size: 13px; }
.pr-actions { display: flex; gap: 8px; margin-top: 12px; }
</style>
