<template>
  <div class="chat-layout">
    <!-- ============ 左侧会话栏 ============ -->
    <aside class="sidebar">
      <div class="sidebar-top">
        <div class="logo-row">
          <img src="/favicon.svg" alt="logo" class="logo" />
          <span class="logo-text">EdAgent</span>
        </div>
        <el-input
          v-model="searchQuery"
          placeholder="搜索会话…"
          :prefix-icon="Search"
          clearable
          size="small"
          class="search-input"
          @input="onSearch"
        />
        <!-- 会话组织维度：全部 / 收藏 + 分类 / 标签筛选 -->
        <el-radio-group v-model="filterFavorite" size="small" class="filter-row" @change="loadConversations">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="fav">收藏</el-radio-button>
        </el-radio-group>
        <div class="filter-selects">
          <el-select
            v-model="filterCategory"
            placeholder="按分类"
            size="small"
            clearable
            @change="loadConversations"
          >
            <el-option
              v-for="c in filterOptions.categories"
              :key="c.name"
              :label="`${c.name}（${c.count}）`"
              :value="c.name"
            />
          </el-select>
          <el-select
            v-model="filterTag"
            placeholder="按标签"
            size="small"
            clearable
            @change="loadConversations"
          >
            <el-option
              v-for="t in filterOptions.tags"
              :key="t.name"
              :label="`${t.name}（${t.count}）`"
              :value="t.name"
            />
          </el-select>
        </div>
        <el-button type="primary" class="new-chat-btn" :icon="Plus" @click="newConversation">
          新建对话
        </el-button>
        <el-button class="team-btn" :icon="User" @click="router.push('/team')">
          团队协作
        </el-button>
      </div>

      <el-scrollbar class="conv-scroll ea-scroll">
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: conv.id === currentId, pinned: conv.pinned }"
          @click="selectConversation(conv.id)"
        >
          <ChatDotRound class="conv-icon" />
          <div class="conv-main">
            <span class="conv-title">{{ conv.title || '新会话' }}</span>
            <span
              v-if="conv.category || (conv.tags || []).length || conv.favorite || conv.share_token"
              class="conv-meta"
            >
              <el-tag v-if="conv.category" size="small" type="warning" effect="plain" round>{{ conv.category }}</el-tag>
              <el-tag
                v-for="t in (conv.tags || []).slice(0, 3)"
                :key="t"
                size="small"
                type="info"
                effect="plain"
                round
              >{{ t }}</el-tag>
              <span v-if="(conv.tags || []).length > 3" class="conv-more-tag">+{{ conv.tags.length - 3 }}</span>
              <StarFilled v-if="conv.favorite" class="fav-mark" title="已收藏" />
              <Share v-if="conv.share_token" class="share-mark" title="已开启协作围观" />
            </span>
          </div>
          <Star
            class="conv-pin"
            :class="{ 'is-pinned': conv.pinned }"
            :title="conv.pinned ? '取消置顶' : '置顶'"
            @click.stop="togglePin(conv)"
          />
          <el-dropdown
            class="conv-more"
            trigger="click"
            @command="(cmd) => onConvCommand(conv, cmd)"
            @click.stop
          >
            <MoreFilled title="更多操作" @click.stop />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="favorite">
                  {{ conv.favorite ? '取消收藏' : '加入收藏' }}
                </el-dropdown-item>
                <el-dropdown-item command="meta">标签 / 分类</el-dropdown-item>
                <el-dropdown-item command="share">分享协作室</el-dropdown-item>
                <el-dropdown-item command="md" divided>导出 Markdown</el-dropdown-item>
                <el-dropdown-item command="html">导出 HTML</el-dropdown-item>
                <el-dropdown-item command="pdf">打印为 PDF</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除会话</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
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
            <el-tag v-if="auth.user?.role === 'admin'" size="small" type="warning" effect="light" round>管理员</el-tag>
            <ArrowDown />
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="auth.user?.role === 'admin'" command="admin" :icon="Setting">管理后台</el-dropdown-item>
              <el-dropdown-item command="logout" :icon="SwitchButton">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <!-- ============ 右侧聊天主区 ============ -->
    <main class="main">
      <header class="main-header">
        <div class="header-left">
          <h2>{{ displayTitle }}</h2>
          <el-tag v-if="sharedMode" type="success" effect="dark" size="small">协作围观（只读）</el-tag>
          <span v-if="sharedMode && sharedOwner" class="collab-owner">发起人：{{ sharedOwner }}</span>
        </div>
        <div class="header-right">
          <div v-if="sharedMode" class="collab-members">
            <el-tooltip
              v-for="m in roomMembers"
              :key="m.id"
              :content="`${m.username}（在线）`"
              placement="bottom"
            >
              <el-avatar :size="24" class="member-avatar">{{ m.username.charAt(0).toUpperCase() }}</el-avatar>
            </el-tooltip>
            <span v-if="roomMembers.length" class="member-count">{{ roomMembers.length }} 人在线</span>
          </div>
          <el-tag v-if="sending" type="warning" effect="plain" size="small">
            <el-icon class="is-loading"><Loading /></el-icon>&nbsp;推理中…
          </el-tag>
        </div>
      </header>

      <el-scrollbar ref="msgScrollRef" class="msg-scroll ea-scroll">
        <div class="msg-inner">
          <!-- 空状态 -->
          <div v-if="!messages.length && !sharedMode" class="welcome">
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

      <!-- 输入区（协作围观模式下只读，隐藏输入框） -->
      <footer v-if="!sharedMode" class="composer">
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
      <footer v-else class="composer readonly-bar">
        <p class="composer-tip">协作围观模式为只读：你可以实时看到发起人的提问与 AI 的逐字输出，但不能在此发送消息。</p>
      </footer>
    </main>

    <!-- ============ 知识库抽屉 ============ -->
    <el-drawer v-model="kbDrawer" title="我的知识库" size="420px">
      <div class="kb-section">
        <el-upload
          drag
          multiple
          :auto-upload="false"
          :show-file-list="true"
          :on-change="onKbFileSelect"
          :on-remove="onKbFileRemove"
          accept=".pdf,.docx,.xlsx,.pptx,.csv,.md,.markdown,.txt,.html,.htm"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
          <template #tip>
            <div class="el-upload__tip">支持 PDF / Word / Excel / PPT / CSV / Markdown / TXT / HTML，单个文件最大 50MB，仅本人可检索</div>
          </template>
        </el-upload>
        <el-button
          v-if="pendingFiles.length"
          type="primary"
          size="small"
          style="margin-top: 8px; width: 100%"
          :loading="batchRunning"
          @click="startBatchUpload"
        >
          开始导入（{{ pendingFiles.length }} 个文件）
        </el-button>
      </div>

      <!-- 批量导入进度 -->
      <div v-if="batchJob" class="batch-progress" style="margin-bottom: 12px">
        <el-progress
          :percentage="batchProgress"
          :status="batchJob.status === 'failed' ? 'exception' : undefined"
          :stroke-width="8"
        />
        <div style="font-size: 12px; color: var(--ea-text-tertiary); margin-top: 4px">
          已完成 {{ batchJob.completed_files }}/{{ batchJob.total_files }}，
          失败 {{ batchJob.failed_files }}
        </div>
        <div v-for="(r, i) in (batchJob.results || [])" :key="i" style="display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:2px 0">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px">{{ r.filename }}</span>
          <el-tag :type="r.status === 'success' ? 'success' : 'danger'" size="small">
            {{ r.status === 'success' ? `${r.chunks}块` : '失败' }}
          </el-tag>
        </div>
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

    <!-- ============ 标签 / 分类编辑 ============ -->
    <el-dialog v-model="metaDialog" title="标签 / 分类" width="400px">
      <el-form label-width="64px">
        <el-form-item label="分类">
          <el-select
            v-model="metaForm.category"
            placeholder="单选，可直接输入新分类"
            filterable
            allow-create
            default-first-option
            clearable
            style="width: 100%"
          >
            <el-option
              v-for="c in filterOptions.categories"
              :key="c.name"
              :label="c.name"
              :value="c.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="metaForm.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="多选，可直接输入新标签（最多 10 个）"
            style="width: 100%"
          >
            <el-option
              v-for="t in filterOptions.tags"
              :key="t.name"
              :label="t.name"
              :value="t.name"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="metaDialog = false">取消</el-button>
        <el-button type="primary" @click="saveMeta">保存</el-button>
      </template>
    </el-dialog>

    <!-- ============ 协作围观分享 ============ -->
    <el-dialog v-model="shareDialog" title="分享协作室" width="460px" @closed="stopSharePolling">
      <p class="share-desc">
        开启后，任何已登录用户凭下面的链接都能以<strong>只读</strong>方式实时围观本会话——
        包括发起人的提问与 AI 的逐字输出。
      </p>
      <el-input v-if="shareToken" v-model="shareUrl" readonly>
        <template #append>
          <el-button :icon="CopyDocument" @click="copyShare">复制</el-button>
        </template>
      </el-input>
      <el-alert
        v-else
        type="info"
        :closable="false"
        show-icon
        title="尚未开启分享"
        description="点击下方「开启分享」生成围观链接。"
      />
      <p v-if="shareToken" class="share-viewers">当前在线围观：{{ shareViewers }} 人</p>
      <template #footer>
        <el-button v-if="shareToken" type="danger" plain @click="revokeShare">关闭分享</el-button>
        <el-button v-else type="primary" @click="enableShare">开启分享</el-button>
        <el-button @click="shareDialog = false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Delete, ChatDotRound, ArrowDown, SwitchButton, FolderOpened,
  Promotion, VideoPause, Loading, ChatLineRound, DataAnalysis, Clock,
  Tools, Document, UploadFilled, Setting, Search, Star, StarFilled, User,
  MoreFilled, Share, CopyDocument,
} from '@element-plus/icons-vue'
import { api, downloadFile, streamChat } from '../api/client'
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
const searchQuery = ref('')
let searchTimer = null

const kbDrawer = ref(false)
const kbStats = ref({ documents: 0 })
const kbFiles = ref([])

// ---------------- 会话组织维度（标签 / 分类 / 收藏） ----------------
const filterFavorite = ref('all')   // all | fav
const filterTag = ref('')
const filterCategory = ref('')
const filterOptions = ref({ tags: [], categories: [] })

const metaDialog = ref(false)
const metaConv = ref(null)
const metaForm = reactive({ category: '', tags: [] })

// ---------------- 协作围观 ----------------
const sharedMode = ref(false)
const sharedToken = ref('')
const sharedOwner = ref('')
const sharedTitle = ref('')
const roomMembers = ref([])
let collabWs = null
let collabPingTimer = null
let liveAssistant = null

const shareDialog = ref(false)
const shareConv = ref(null)
const shareToken = ref('')
const shareUrl = ref('')
const shareViewers = ref(0)
let shareViewerTimer = null

const routeMap = { simple: '直接回答', tool: '工具调用', rag: '知识库', team: '多Agent协作' }
const toolNameMap = {
  calculator: '计算器',
  current_time: '当前时间',
  word_count: '字数统计',
  knowledge_search: '知识库检索',
}

const activeTitle = computed(
  () => conversations.value.find((c) => c.id === currentId.value)?.title
)

const displayTitle = computed(() =>
  sharedMode.value
    ? sharedTitle.value || '共享会话'
    : activeTitle.value || 'EdAgent 本地助手'
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
  const params = new URLSearchParams()
  if (searchQuery.value.trim()) params.set('q', searchQuery.value.trim())
  if (filterTag.value) params.set('tag', filterTag.value)
  if (filterCategory.value) params.set('category', filterCategory.value)
  if (filterFavorite.value === 'fav') params.set('favorite', '1')
  const qs = params.toString()
  const data = await api.get(`/api/v1/ui/conversations${qs ? `?${qs}` : ''}`)
  conversations.value = data.items
}

/** 拉取当前用户已用过的分类 / 标签（含计数），供筛选与编辑下拉使用。 */
async function loadFilters() {
  try {
    filterOptions.value = await api.get('/api/v1/ui/conversation-filters')
  } catch {
    // 筛选下拉失败不影响会话主流程
  }
}

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(loadConversations, 300)
}

async function togglePin(conv) {
  try {
    const result = await api.post(`/api/v1/ui/conversations/${conv.id}/pin`, {})
    conv.pinned = result.pinned
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function selectConversation(id) {
  if (sending.value || id === currentId.value) return
  if (sharedMode.value) closeCollab()
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
  if (sharedMode.value) closeCollab()
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
    loadFilters()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

/** 会话行「更多操作」下拉路由：收藏 / 标签分类 / 分享 / 导出 / 删除。 */
function onConvCommand(conv, command) {
  switch (command) {
    case 'favorite':
      toggleFavorite(conv)
      break
    case 'meta':
      openMetaDialog(conv)
      break
    case 'share':
      openShareDialog(conv)
      break
    case 'delete':
      confirmDeleteConversation(conv)
      break
    default:
      exportConversation(conv, command)
  }
}

async function toggleFavorite(conv) {
  try {
    const result = await api.patch(`/api/v1/ui/conversations/${conv.id}/meta`, {
      favorite: !conv.favorite,
    })
    Object.assign(conv, result.conversation)
    // 「仅收藏」视图下取消收藏后即刻从列表移除
    if (filterFavorite.value === 'fav' && !conv.favorite) {
      conversations.value = conversations.value.filter((c) => c.id !== conv.id)
    }
    ElMessage.success(conv.favorite ? '已加入收藏' : '已取消收藏')
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function openMetaDialog(conv) {
  metaConv.value = conv
  metaForm.category = conv.category || ''
  metaForm.tags = [...(conv.tags || [])]
  metaDialog.value = true
}

async function saveMeta() {
  const conv = metaConv.value
  if (!conv) return
  try {
    const result = await api.patch(`/api/v1/ui/conversations/${conv.id}/meta`, {
      category: metaForm.category || '',
      tags: metaForm.tags,
    })
    Object.assign(conv, result.conversation)
    metaDialog.value = false
    ElMessage.success('标签 / 分类已保存')
    await Promise.all([loadFilters(), loadConversations()])
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function confirmDeleteConversation(conv) {
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${conv.title || '新会话'}」及其全部消息？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return // 用户取消
  }
  await deleteConversation(conv.id)
}

/** 导出会话（format: md / html / pdf） */
async function exportConversation(conv, format = 'md') {
  if (format === 'pdf') {
    // PDF：打开 HTML 导出页面，由用户 Ctrl+P 打印为 PDF
    const url = `/api/v1/ui/conversations/${conv.id}/export?format=html`
    window.open(url, '_blank')
    return
  }
  try {
    const name = await downloadFile(`/api/v1/ui/conversations/${conv.id}/export?format=${format}`)
    ElMessage.success(`已导出：${name}`)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

// ---------------- 协作围观：分享链接管理 ----------------

function absoluteUrl(path) {
  return new URL(path, window.location.origin).href
}

async function openShareDialog(conv) {
  shareConv.value = conv
  shareDialog.value = true
  await refreshShareState()
  startSharePolling()
}

/** 拉取当前会话的分享状态（token / 链接 / 在线围观人数）。 */
async function refreshShareState() {
  if (!shareConv.value) return
  try {
    const data = await api.get(`/api/v1/ui/conversations/${shareConv.value.id}/share`)
    applyShareState(data)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function applyShareState(data) {
  shareToken.value = data.share_token || ''
  shareUrl.value = data.share_url ? absoluteUrl(data.share_url) : ''
  shareViewers.value = data.viewers || 0
  if (shareConv.value) shareConv.value.share_token = data.share_token || null
}

async function enableShare() {
  if (!shareConv.value) return
  try {
    applyShareState(await api.post(`/api/v1/ui/conversations/${shareConv.value.id}/share`, {}))
    ElMessage.success('已开启协作围观')
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function revokeShare() {
  if (!shareConv.value) return
  try {
    await api.delete(`/api/v1/ui/conversations/${shareConv.value.id}/share`)
    applyShareState({ share_token: null, share_url: null, viewers: 0 })
    ElMessage.success('已关闭分享')
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function copyShare() {
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    ElMessage.success('链接已复制')
  } catch {
    ElMessage.warning('复制失败，请手动全选链接复制')
  }
}

function startSharePolling() {
  stopSharePolling()
  shareViewerTimer = setInterval(refreshShareState, 3000)
}

function stopSharePolling() {
  clearInterval(shareViewerTimer)
  shareViewerTimer = null
}

// ---------------- 协作围观：只读实时视图 ----------------

/** 进入围观模式：拉历史消息 + 建立 WebSocket 订阅。 */
async function enterSharedMode(token) {
  try {
    const conv = await api.get(`/api/v1/ui/shared/${token}`)
    sharedMode.value = true
    sharedToken.value = token
    sharedTitle.value = conv.title
    sharedOwner.value = conv.owner_username || ''
    currentId.value = conv.id
    messages.value = conv.messages.map((m) => ({ ...m, streaming: false }))
    await scrollToBottom()
    connectCollab(token)
  } catch (err) {
    sharedMode.value = false
    ElMessage.error(err.message)
  }
}

/** 退出围观模式：断开 WebSocket 并清空协作态。 */
function closeCollab() {
  if (collabWs) {
    try {
      collabWs.close()
    } catch {
      // 忽略关闭异常
    }
    collabWs = null
  }
  clearInterval(collabPingTimer)
  collabPingTimer = null
  liveAssistant = null
  roomMembers.value = []
  sharedMode.value = false
  sharedToken.value = ''
  sharedOwner.value = ''
  sharedTitle.value = ''
}

function connectCollab(token) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${window.location.host}/api/v1/ui/ws/${token}`)
  collabWs = ws

  ws.onmessage = (event) => {
    let payload
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }
    handleCollabEvent(payload)
  }
  ws.onclose = (event) => {
    if (collabWs !== ws) return
    collabWs = null
    clearInterval(collabPingTimer)
    collabPingTimer = null
    if (sharedMode.value) {
      const fatal = event.code === 4401 || event.code === 4404
      ElMessage.warning(fatal ? '围观链接无效或已被关闭' : '协作连接已断开')
      if (!fatal) sharedMode.value = false
    }
  }
  // 轻量心跳：避免长空闲连接被中间层回收
  collabPingTimer = setInterval(() => {
    if (collabWs && collabWs.readyState === WebSocket.OPEN) collabWs.send('ping')
  }, 25000)
}

function handleCollabEvent(event) {
  switch (event.type) {
    case 'history':
      messages.value = (event.messages || []).map((m) => ({ ...m, streaming: false }))
      sharedTitle.value = event.title || sharedTitle.value
      sharedOwner.value = event.owner || sharedOwner.value
      scrollToBottom()
      break
    case 'presence':
      roomMembers.value = event.members || []
      break
    case 'join':
      ElMessage.info(`${event.username} 加入了围观`)
      break
    case 'leave':
      ElMessage.info(`${event.username} 离开了围观`)
      break
    case 'turn_start':
      // 发起人开始新一轮问答：插入提问 + 空的流式回复占位
      liveAssistant = reactive({
        role: 'assistant',
        content: '',
        reasoning: '',
        sources: [],
        tool_results: [],
        route: null,
        streaming: true,
      })
      messages.value.push({ role: 'user', content: event.message })
      messages.value.push(liveAssistant)
      sending.value = true
      scrollToBottom()
      break
    case 'reasoning':
      if (liveAssistant) {
        liveAssistant.reasoning += event.delta || ''
        scrollToBottom()
      }
      break
    case 'token':
      if (liveAssistant) {
        liveAssistant.content += event.delta || ''
        scrollToBottom()
      }
      break
    case 'turn_done':
      if (liveAssistant) {
        liveAssistant.content = event.answer || liveAssistant.content
        liveAssistant.route = event.route
        liveAssistant.sources = event.sources || []
        liveAssistant.tool_results = event.tool_results || []
        liveAssistant.streaming = false
      }
      liveAssistant = null
      sending.value = false
      scrollToBottom()
      break
    case 'turn_error':
      if (liveAssistant) {
        liveAssistant.streaming = false
        liveAssistant.content += `\n\n> ⚠️ ${event.message}`
      }
      liveAssistant = null
      sending.value = false
      ElMessage.error(event.message)
      break
    case 'error':
      ElMessage.error(event.message)
      break
  }
}

async function onUserCommand(command) {
  if (command === 'admin') {
    router.push('/admin')
  } else if (command === 'logout') {
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
  if (sharedMode.value) {
    ElMessage.info('协作围观模式为只读，无法发送消息')
    return
  }
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

// ==================== 知识库批量导入 ====================

const pendingFiles = ref([])
const batchJob = ref(null)
let batchPollTimer = null

const batchRunning = computed(() => batchJob.value && batchJob.value.status === 'running')
const batchProgress = computed(() => {
  if (!batchJob.value) return 0
  const total = batchJob.value.total_files || 1
  return Math.round(((batchJob.value.completed_files + batchJob.value.failed_files) / total) * 100)
})

function onKbFileSelect(uploadFile) {
  // 收集待上传文件
  pendingFiles.value.push(uploadFile.raw)
}

function onKbFileRemove(uploadFile) {
  pendingFiles.value = pendingFiles.value.filter(f => f !== uploadFile.raw)
}

async function startBatchUpload() {
  if (!pendingFiles.value.length) return
  const formData = new FormData()
  for (const f of pendingFiles.value) formData.append('files', f)
  try {
    const result = await api.upload('/api/v1/rag/batch', formData)
    batchJob.value = result
    ElMessage.success(`已创建批量任务，共 ${result.total_files} 个文件`)
    pendingFiles.value = []
    // 清空 el-upload 文件列表
    pollBatchStatus(result.job_id)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function pollBatchStatus(jobId) {
  clearInterval(batchPollTimer)
  batchPollTimer = setInterval(async () => {
    try {
      const job = await api.get(`/api/v1/rag/batch/${jobId}`)
      batchJob.value = job
      if (job.status !== 'running') {
        clearInterval(batchPollTimer)
        ElMessage.success(`批量导入完成：成功 ${job.completed_files}，失败 ${job.failed_files}`)
        await loadKb()
      }
    } catch {
      clearInterval(batchPollTimer)
    }
  }, 1000)
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
  await Promise.all([loadConversations(), loadFilters()])
  // 分享链接进入协作围观（只读）
  const shareQuery = route.query.share
  if (typeof shareQuery === 'string' && shareQuery) {
    await enterSharedMode(shareQuery)
    return
  }
  const cid = route.query.c
  if (cid && conversations.value.some((c) => c.id === cid)) {
    await selectConversation(cid)
  }
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  clearInterval(batchPollTimer)
  clearInterval(shareViewerTimer)
  closeCollab()
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
.team-btn {
  width: 100%;
  margin-bottom: 8px;
}
.search-input {
  width: 100%;
  margin-bottom: 8px;
}

/* 会话组织维度：筛选控件 */
.filter-row {
  width: 100%;
  margin-bottom: 6px;
}
.filter-row :deep(.el-radio-button) {
  flex: 1;
}
.filter-row :deep(.el-radio-button__inner) {
  width: 100%;
}
.filter-selects {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
}
.filter-selects :deep(.el-select) {
  flex: 1;
  min-width: 0;
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
.conv-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.conv-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow: hidden;
  white-space: nowrap;
}
.conv-meta :deep(.el-tag) {
  height: 17px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 15px;
  max-width: 72px;
  overflow: hidden;
}
.conv-more-tag {
  font-size: 11px;
  color: #97a0b8;
}
.fav-mark {
  flex-shrink: 0;
  font-size: 12px;
  color: #e6a23c;
}
.share-mark {
  flex-shrink: 0;
  font-size: 12px;
  color: #67c23a;
}
.conv-more {
  opacity: 0;
  font-size: 14px;
  color: #97a0b8;
  transition: opacity 0.15s;
  flex-shrink: 0;
}
.conv-item:hover .conv-more {
  opacity: 1;
}
.conv-more:hover {
  color: var(--ea-primary);
}
.conv-pin {
  opacity: 0;
  font-size: 14px;
  color: #97a0b8;
  transition: opacity 0.15s;
  flex-shrink: 0;
}
.conv-pin.is-pinned {
  opacity: 1;
  color: #e6a23c;
}
.conv-item:hover .conv-pin {
  opacity: 1;
}
.conv-item.pinned {
  background: rgba(230, 162, 60, 0.08);
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
.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.header-left h2 {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collab-owner {
  font-size: 12px;
  color: var(--ea-text-soft);
}
/* 协作围观在线成员 */
.collab-members {
  display: flex;
  align-items: center;
}
.collab-members .member-avatar {
  background: #67c23a;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  margin-left: -6px;
  border: 2px solid var(--ea-panel);
}
.collab-members .member-avatar:first-child {
  margin-left: 0;
}
.member-count {
  margin-left: 8px;
  font-size: 12px;
  color: var(--ea-text-soft);
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
/* 协作围观只读提示条 */
.readonly-bar {
  border-top: 1px solid var(--ea-border);
}
.readonly-bar .composer-tip {
  color: #67c23a;
}
/* 分享弹窗 */
.share-desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ea-text-soft);
}
.share-viewers {
  margin: 12px 0 0;
  font-size: 13px;
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
