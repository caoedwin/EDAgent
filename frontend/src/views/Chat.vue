<template>
  <div class="chat-layout">
    <!-- ============ 左侧会话栏 ============ -->
    <aside class="sidebar">
      <div class="sidebar-top">
        <div class="logo-row">
          <img src="/favicon.svg" alt="logo" class="logo" />
          <span class="logo-text">EdAgent</span>
        </div>
        <el-button type="primary" class="new-chat-btn" :icon="Plus" @click="newConversation">
          新建对话
        </el-button>
      </div>

      <el-scrollbar class="conv-scroll ea-scroll">
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: conv.id === currentId }"
          @click="selectConversation(conv.id)"
        >
          <ChatDotRound class="conv-icon" />
          <span class="conv-title">{{ conv.title || '新会话' }}</span>
          <el-popconfirm title="删除该会话及其全部消息？" @confirm="deleteConversation(conv.id)">
            <template #reference>
              <Delete class="conv-del" @click.stop />
            </template>
          </el-popconfirm>
        </div>
        <el-empty v-if="!conversations.length" description="还没有对话" :image-size="60" />
      </el-scrollbar>

      <div class="sidebar-bottom">
        <div class="side-link" @click="kbDrawer = true; loadKb()">
          <FolderOpened />
          <span>我的知识库</span>
          <el-tag size="small" type="info" effect="dark" round>{{ kbStats.documents || 0 }} 块</el-tag>
        </div>
        <el-dropdown trigger="click" @command="onUserCommand">
          <div class="side-link user-row">
            <el-avatar :size="28" class="user-avatar">{{ username.charAt(0).toUpperCase() }}</el-avatar>
            <span class="user-name">{{ username }}</span>
            <ArrowDown />
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout" :icon="SwitchButton">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <!-- ============ 右侧聊天主区 ============ -->
    <main class="main">
      <header class="main-header">
        <h2>{{ activeTitle || 'EdAgent 本地助手' }}</h2>
        <el-tag v-if="sending" type="warning" effect="plain" size="small">
          <el-icon class="is-loading"><Loading /></el-icon>&nbsp;推理中…
        </el-tag>
      </header>

      <el-scrollbar ref="msgScrollRef" class="msg-scroll ea-scroll">
        <div class="msg-inner">
          <!-- 空状态 -->
          <div v-if="!messages.length" class="welcome">
            <img src="/favicon.svg" alt="" class="welcome-logo" />
            <h1>你好，{{ username }}</h1>
            <p>我是运行在你本地的 EdAgent，可以直接问答、调用工具，也能基于你上传的文档回答问题。</p>
            <div class="suggest-row">
              <div class="suggest-card" @click="quickSend('用一句话介绍一下你自己')">
                <ChatLineRound />
                <span>用一句话介绍你自己</span>
              </div>
              <div class="suggest-card" @click="quickSend('帮我计算 (128 + 256) * 3 等于多少')">
                <DataAnalysis />
                <span>计算 (128 + 256) × 3</span>
              </div>
              <div class="suggest-card" @click="quickSend('现在是什么时间？')">
                <Clock />
                <span>查询当前时间</span>
              </div>
            </div>
          </div>

          <!-- 消息列表 -->
          <div
            v-for="(msg, idx) in messages"
            :key="idx"
            class="msg-row"
            :class="msg.role"
          >
            <div class="avatar">
              <img v-if="msg.role === 'assistant'" src="/favicon.svg" alt="ai" />
              <span v-else>{{ username.charAt(0).toUpperCase() }}</span>
            </div>
            <div class="bubble-wrap">
              <div class="bubble" :class="msg.role">
                <!-- 思考过程 -->
                <details
                  v-if="msg.reasoning"
                  class="reasoning"
                  :open="msg.streaming"
                >
                  <summary>思考过程{{ msg.streaming ? '' : '（点击展开）' }}</summary>
                  <div class="reasoning-text">{{ msg.reasoning }}</div>
                </details>

                <!-- 正文 -->
                <div v-if="msg.content" class="markdown-body" v-html="renderMarkdown(msg.content)"></div>
                <span v-else-if="msg.streaming" class="typing-cursor">▍</span>

                <!-- 工具调用记录 -->
                <div v-if="msg.tool_results && msg.tool_results.length" class="meta-block">
                  <div v-for="(tool, ti) in msg.tool_results" :key="ti" class="tool-chip">
                    <el-icon><Tools /></el-icon>
                    <b>{{ toolNameMap[tool.tool] || tool.tool }}</b>
                    <span class="tool-args" v-if="hasArgs(tool.args)">{{ shortArgs(tool.args) }}</span>
                    <el-tag size="small" :type="tool.status === 'success' ? 'success' : 'danger'">
                      {{ tool.status }}
                    </el-tag>
                  </div>
                </div>

                <!-- 引用来源 -->
                <div v-if="msg.sources && msg.sources.length" class="sources">
                  <span class="sources-label">参考来源：</span>
                  <el-tag
                    v-for="(src, si) in dedupeSources(msg.sources)"
                    :key="si"
                    size="small"
                    effect="plain"
                    class="source-tag"
                  >
                    <el-icon><Document /></el-icon>&nbsp;{{ src.original_name || src.source }}
                    <template v-if="src.page"> · 第 {{ src.page }} 页</template>
                  </el-tag>
                </div>
              </div>
              <div v-if="msg.route && msg.role === 'assistant'" class="route-label">
                <el-tag size="small" effect="plain" round>{{ routeMap[msg.route] || msg.route }}</el-tag>
              </div>
            </div>
          </div>
        </div>
      </el-scrollbar>

      <!-- 输入区 -->
      <footer class="composer">
        <div class="composer-box">
          <el-input
            v-model="draft"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
            resize="none"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行…"
            :disabled="sending"
            @keydown.enter.exact.prevent="send"
          />
          <div class="composer-actions">
            <el-button v-if="!sending" type="primary" circle :icon="Promotion" @click="send" />
            <el-button v-else type="danger" circle :icon="VideoPause" @click="stop" />
          </div>
        </div>
        <p class="composer-tip">模型在本地 CPU 运行，首次响应可能需要等待 1-2 分钟。</p>
      </footer>
    </main>

    <!-- ============ 知识库抽屉 ============ -->
    <el-drawer v-model="kbDrawer" title="我的知识库" size="420px">
      <div class="kb-section">
        <el-upload
          drag
          multiple
          :auto-upload="true"
          :show-file-list="false"
          :http-request="uploadKbFile"
          accept=".pdf,.md,.markdown,.txt,.html,.htm"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或<em>点击上传</em></div>
          <template #tip>
            <div class="el-upload__tip">支持 PDF / Markdown / TXT / HTML，单个文件最大 50MB，仅本人可检索</div>
          </template>
        </el-upload>
      </div>

      <div class="kb-stats">
        共 <b>{{ kbStats.documents || 0 }}</b> 个分块，<b>{{ kbFiles.length }}</b> 个文件
      </div>

      <el-table :data="kbFiles" size="small" class="kb-table">
        <el-table-column prop="original_name" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="chunks" label="分块" width="70" align="center" />
        <el-table-column label="操作" width="70" align="center">
          <template #default="{ row }">
            <el-popconfirm title="删除该文档？" @confirm="deleteKbFile(row.filename)">
              <template #reference>
                <el-button link type="danger" :icon="Delete" />
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ElMessage } from 'element-plus'
import {
  Plus, Delete, ChatDotRound, ArrowDown, SwitchButton, FolderOpened,
  Promotion, VideoPause, Loading, ChatLineRound, DataAnalysis, Clock,
  Tools, Document, UploadFilled,
} from '@element-plus/icons-vue'
import { api, streamChat } from '../api/client'
import { useAuthStore } from '../stores/auth'

marked.setOptions({ breaks: true, gfm: true })

const auth = useAuthStore()
const username = computed(() => auth.username)
const route = useRoute()
const router = useRouter()

const conversations = ref([])
const currentId = ref(null)
const messages = ref([])
const draft = ref('')
const sending = ref(false)
let abortController = null
const msgScrollRef = ref()

const kbDrawer = ref(false)
const kbStats = ref({ documents: 0 })
const kbFiles = ref([])

const routeMap = { simple: '直接回答', tool: '工具调用', rag: '知识库' }
const toolNameMap = {
  calculator: '计算器',
  current_time: '当前时间',
  word_count: '字数统计',
  knowledge_search: '知识库检索',
}

const activeTitle = computed(
  () => conversations.value.find((c) => c.id === currentId.value)?.title
)

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
}

function dedupeSources(sources) {
  const seen = new Set()
  return sources.filter((s) => {
    const key = `${s.source}-${s.page || 0}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function hasArgs(args) {
  return args && Object.keys(args).length
}

function shortArgs(args) {
  const text = Object.values(args).join('，')
  return text.length > 40 ? text.slice(0, 40) + '…' : text
}

async function scrollToBottom() {
  await nextTick()
  const wrap = msgScrollRef.value?.wrapRef
  if (wrap) wrap.scrollTop = wrap.scrollHeight
}

// ---------------- 会话管理 ----------------

async function loadConversations() {
  const data = await api.get('/api/v1/ui/conversations')
  conversations.value = data.items
}

async function selectConversation(id) {
  if (sending.value || id === currentId.value) return
  try {
    const conv = await api.get(`/api/v1/ui/conversations/${id}`)
    currentId.value = id
    messages.value = conv.messages.map((m) => ({ ...m, streaming: false }))
    router.replace({ query: { c: id } })
    await scrollToBottom()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function newConversation() {
  if (sending.value) return
  currentId.value = null
  messages.value = []
  router.replace({ query: {} })
}

async function deleteConversation(id) {
  try {
    await api.delete(`/api/v1/ui/conversations/${id}`)
    conversations.value = conversations.value.filter((c) => c.id !== id)
    if (currentId.value === id) newConversation()
    ElMessage.success('会话已删除')
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function onUserCommand(command) {
  if (command === 'logout') {
    await auth.logout()
    router.push('/login')
  }
}

// ---------------- 发送与流式接收 ----------------

function quickSend(text) {
  draft.value = text
  send()
}

async function send() {
  const text = draft.value.trim()
  if (!text || sending.value) return
  draft.value = ''

  messages.value.push({ role: 'user', content: text })
  const assistantMsg = reactive({
    role: 'assistant',
    content: '',
    reasoning: '',
    sources: [],
    tool_results: [],
    route: null,
    streaming: true,
  })
  messages.value.push(assistantMsg)
  sending.value = true
  await scrollToBottom()

  abortController = streamChat({
    message: text,
    conversationId: currentId.value,
    handlers: {
      onMeta: async (payload) => {
        currentId.value = payload.conversation_id
        router.replace({ query: { c: payload.conversation_id } })
        await loadConversations()
      },
      onReasoning: (delta) => {
        assistantMsg.reasoning += delta
        scrollToBottom()
      },
      onToken: (delta) => {
        assistantMsg.content += delta
        scrollToBottom()
      },
      onDone: (payload) => {
        assistantMsg.content = payload.answer || assistantMsg.content
        assistantMsg.route = payload.route
        assistantMsg.sources = payload.sources || []
        assistantMsg.tool_results = payload.tool_results || []
        assistantMsg.streaming = false
        sending.value = false
        loadConversations()
        scrollToBottom()
      },
      onError: (message) => {
        assistantMsg.streaming = false
        assistantMsg.content += `\n\n> ⚠️ ${message}`
        sending.value = false
        ElMessage.error(message)
      },
    },
  })
}

function stop() {
  abortController?.abort()
  sending.value = false
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant') last.streaming = false
  ElMessage.info('已停止生成')
}

// ---------------- 知识库 ----------------

async function loadKb() {
  try {
    const stats = await api.get('/api/v1/rag/stats')
    kbStats.value = stats
    kbFiles.value = stats.files || []
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function uploadKbFile(options) {
  const formData = new FormData()
  formData.append('file', options.file)
  try {
    const result = await api.upload('/api/v1/rag/documents', formData)
    ElMessage.success(`「${result.original_name}」入库成功，${result.chunks} 个分块`)
    await loadKb()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function deleteKbFile(filename) {
  try {
    await api.delete(`/api/v1/rag/documents/${encodeURIComponent(filename)}`)
    ElMessage.success('文档已删除')
    await loadKb()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ---------------- 初始化（支持刷新恢复） ----------------

onMounted(async () => {
  await loadConversations()
  const cid = route.query.c
  if (cid && conversations.value.some((c) => c.id === cid)) {
    await selectConversation(cid)
  }
})
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
}

/* 侧栏 */
.sidebar {
  width: 264px;
  flex-shrink: 0;
  background: var(--ea-sidebar);
  color: #d7dcea;
  display: flex;
  flex-direction: column;
}
.sidebar-top {
  padding: 16px 14px 10px;
}
.logo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px 14px;
}
.logo {
  width: 30px;
  height: 30px;
}
.logo-text {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.new-chat-btn {
  width: 100%;
}
.conv-scroll {
  flex: 1;
  padding: 6px 8px;
}
.conv-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13.5px;
  color: #b8bfd4;
  transition: background 0.15s;
}
.conv-item:hover {
  background: var(--ea-sidebar-hover);
}
.conv-item.active {
  background: var(--ea-sidebar-active);
  color: #fff;
}
.conv-icon {
  flex-shrink: 0;
  font-size: 15px;
}
.conv-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-del {
  opacity: 0;
  font-size: 14px;
  color: #97a0b8;
  transition: opacity 0.15s;
}
.conv-item:hover .conv-del {
  opacity: 1;
}
.conv-del:hover {
  color: #f56c6c;
}
.sidebar-bottom {
  border-top: 1px solid #2a2e3d;
  padding: 8px;
}
.side-link {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13.5px;
  color: #b8bfd4;
}
.side-link:hover {
  background: var(--ea-sidebar-hover);
}
.user-avatar {
  background: var(--ea-primary);
  font-weight: 700;
}
.user-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 主区 */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.main-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  background: var(--ea-panel);
  border-bottom: 1px solid var(--ea-border);
}
.main-header h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.msg-scroll {
  flex: 1;
}
.msg-inner {
  max-width: 860px;
  margin: 0 auto;
  padding: 24px 24px 12px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

/* 欢迎区 */
.welcome {
  text-align: center;
  padding: 70px 20px 30px;
}
.welcome-logo {
  width: 64px;
  height: 64px;
}
.welcome h1 {
  margin: 14px 0 8px;
  font-size: 24px;
}
.welcome p {
  color: var(--ea-text-soft);
  margin: 0 auto 28px;
  max-width: 460px;
}
.suggest-row {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}
.suggest-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--ea-panel);
  border: 1px solid var(--ea-border);
  border-radius: 10px;
  cursor: pointer;
  font-size: 13.5px;
  transition: all 0.15s;
}
.suggest-card:hover {
  border-color: var(--ea-primary);
  color: var(--ea-primary);
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(59, 110, 245, 0.12);
}

/* 消息气泡 */
.msg-row {
  display: flex;
  gap: 12px;
}
.msg-row.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
  background: #e8eefc;
  color: var(--ea-primary);
  overflow: hidden;
}
.avatar img {
  width: 100%;
  height: 100%;
}
.msg-row.user .avatar {
  background: var(--ea-primary);
  color: #fff;
}
.bubble-wrap {
  max-width: 78%;
  min-width: 0;
}
.bubble {
  padding: 11px 15px;
  border-radius: var(--ea-radius);
  font-size: 14.5px;
  line-height: 1.7;
}
.bubble.user {
  background: var(--ea-user-bubble);
  color: #fff;
  border-top-right-radius: 4px;
}
.bubble.assistant {
  background: var(--ea-bot-bubble);
  border: 1px solid var(--ea-border);
  border-top-left-radius: 4px;
}
.route-label {
  margin-top: 5px;
}
.msg-row.user .route-label {
  text-align: right;
}

/* 思考过程 */
.reasoning {
  background: #f8fafc;
  border: 1px solid #edf0f6;
  border-radius: 8px;
  padding: 7px 11px;
  margin-bottom: 9px;
  font-size: 13px;
}
.reasoning summary {
  cursor: pointer;
  color: var(--ea-text-soft);
  user-select: none;
}
.reasoning-text {
  margin-top: 7px;
  color: #64748b;
  white-space: pre-wrap;
  max-height: 260px;
  overflow-y: auto;
}
.typing-cursor {
  animation: blink 1s steps(1) infinite;
  color: var(--ea-primary);
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}

/* 工具与来源 */
.meta-block {
  margin-top: 9px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tool-chip {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12.5px;
  background: #f4f7ff;
  border: 1px solid #e2eaff;
  border-radius: 7px;
  padding: 5px 9px;
}
.tool-args {
  color: var(--ea-text-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sources {
  margin-top: 9px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.sources-label {
  font-size: 12.5px;
  color: var(--ea-text-soft);
}
.source-tag {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 输入区 */
.composer {
  padding: 10px 24px 14px;
  background: linear-gradient(transparent, var(--ea-bg) 30%);
}
.composer-box {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: var(--ea-panel);
  border: 1px solid var(--ea-border);
  border-radius: 14px;
  padding: 9px 10px 9px 16px;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05);
}
.composer-box :deep(.el-textarea__inner) {
  box-shadow: none !important;
  border: none !important;
  padding: 6px 0;
  background: transparent;
}
.composer-actions {
  padding-bottom: 2px;
}
.composer-tip {
  max-width: 860px;
  margin: 7px auto 0;
  text-align: center;
  font-size: 12px;
  color: var(--ea-text-soft);
}

/* 知识库抽屉 */
.kb-section {
  margin-bottom: 16px;
}
.kb-stats {
  font-size: 13px;
  color: var(--ea-text-soft);
  margin-bottom: 10px;
}
.kb-table {
  width: 100%;
}
</style>
