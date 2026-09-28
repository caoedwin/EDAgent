// 统一 fetch 封装：同源 Cookie 会话自动携带；401 时广播跳登录事件。

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `请求失败（${status}）`)
    this.status = status
  }
}

async function request(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: options.body instanceof FormData
      ? {}
      : { 'Content-Type': 'application/json' },
    ...options,
  })

  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('ea:unauthorized'))
  }

  const contentType = res.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await res.json() : await res.text()

  if (!res.ok) {
    const detail = data && typeof data === 'object' ? data.detail : data
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return data
}

export const api = {
  get: (path) => request(path),
  post: (path, body) =>
    request(path, { method: 'POST', body: JSON.stringify(body || {}) }),
  patch: (path, body) =>
    request(path, { method: 'PATCH', body: JSON.stringify(body || {}) }),
  delete: (path) => request(path, { method: 'DELETE' }),
  upload: (path, formData) => request(path, { method: 'POST', body: formData }),
}

/**
 * 文件下载：触发浏览器另存为，文件名优先取 Content-Disposition。
 */
export async function downloadFile(path) {
  const res = await fetch(path, { credentials: 'same-origin' })
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('ea:unauthorized'))
    throw new Error('登录已失效，请重新登录')
  }
  if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`)

  const disposition = res.headers.get('content-disposition') || ''
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  let name = 'export.md'
  if (utf8) name = decodeURIComponent(utf8[1])
  else if (plain) name = plain[1]

  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
  return name
}

/**
 * SSE 流式聊天（POST + ReadableStream 手动解析）。
 * handlers: { onMeta, onReasoning, onToken, onDone, onError }
 * 返回 AbortController，可调用 .abort() 停止生成。
 */
export function streamChat({ message, conversationId, handlers }) {
  const controller = new AbortController()

  ;(async () => {
    let res
    try {
      res = await fetch('/api/v1/ui/chat/stream', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, conversation_id: conversationId }),
        signal: controller.signal,
      })
    } catch (err) {
      if (err.name !== 'AbortError') handlers.onError?.(String(err))
      return
    }

    if (!res.ok || !res.body) {
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent('ea:unauthorized'))
        handlers.onError?.('登录已失效，请重新登录')
      } else {
        handlers.onError?.(`服务异常（HTTP ${res.status}）`)
      }
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    const dispatch = (rawEvent) => {
      let event = 'message'
      const dataLines = []
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!dataLines.length) return
      let payload
      try {
        payload = JSON.parse(dataLines.join('\n'))
      } catch {
        payload = { raw: dataLines.join('\n') }
      }
      switch (event) {
        case 'meta':
          handlers.onMeta?.(payload)
          break
        case 'reasoning':
          handlers.onReasoning?.(payload.delta || '')
          break
        case 'token':
          handlers.onToken?.(payload.delta || '')
          break
        case 'done':
          handlers.onDone?.(payload)
          break
        case 'error':
          handlers.onError?.(payload.message || '生成失败')
          break
      }
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let separator
        while ((separator = buffer.indexOf('\n\n')) >= 0) {
          dispatch(buffer.slice(0, separator))
          buffer = buffer.slice(separator + 2)
        }
      }
      if (buffer.trim()) dispatch(buffer)
    } catch (err) {
      if (err.name !== 'AbortError') handlers.onError?.(String(err))
    }
  })()

  return controller
}
