import fetch from '@blueos.network.fetch'
import storage from '@blueos.storage.storage'
import { MONITOR_URL, MONITOR_TOKEN } from '../monitor.local.js'

export const POLL_INTERVAL_MS = 15000
export const ACTIVE_TASKS_KEY = 'codexPulse.activeTasks.v1'
export const LAST_COMPLETION_KEY = 'codexPulse.lastCompletion.v1'

const MONITOR_URL_KEY = 'codexPulse.monitorUrl.v1'
const MONITOR_TOKEN_KEY = 'codexPulse.monitorToken.v1'

export function readSetting(key, fallback) {
  try {
    const value = storage.getSync({ key: key })
    return value ? String(value) : fallback
  } catch (error) {
    return fallback
  }
}

export function getMonitorConfig() {
  return {
    url: readSetting(MONITOR_URL_KEY, MONITOR_URL),
    token: readSetting(MONITOR_TOKEN_KEY, MONITOR_TOKEN)
  }
}

function parseSnapshot(raw) {
  if (typeof raw !== 'string') {
    return raw
  }
  try {
    return JSON.parse(raw)
  } catch (error) {
    return null
  }
}

function describeFailure(data) {
  if (typeof data === 'string') {
    return data.slice(0, 80)
  }
  if (!data || typeof data !== 'object') {
    return ''
  }
  const message = data.message || data.errMsg || data.errorMessage || data.detail
  return message ? String(message).slice(0, 80) : ''
}

function networkError(data, code) {
  const errorCode = Number(code)
  return {
    type: 'network',
    code: isNaN(errorCode) ? 0 : errorCode,
    message: describeFailure(data)
  }
}

// Keep request details in one place so the foreground page and the background
// completion watcher always use the same URL, token header and validation.
export function requestSnapshot(config, callbacks) {
  const handlers = callbacks || {}
  if (!config || !config.url) {
    return false
  }

  const headers = { 'Accept': 'application/json' }
  if (config.token) {
    headers['X-Codex-Watch-Token'] = config.token
  }

  fetch.fetch({
    url: config.url,
    method: 'GET',
    header: headers,
    // Text is more reliable across simulator and real-watch runtime versions.
    // parseSnapshot still accepts an object if a runtime decodes JSON for us.
    responseType: 'text',
    success: (response) => {
      const code = Number(response && response.code)
      const snapshot = parseSnapshot(response && response.data)
      if (code < 200 || code >= 300) {
        if (handlers.fail) {
          handlers.fail({ type: 'http', code: isNaN(code) ? 0 : code })
        }
        return
      }
      if (!snapshot) {
        if (handlers.fail) {
          handlers.fail({ type: 'format', code: code })
        }
        return
      }
      if (!snapshot.ok) {
        if (handlers.fail) {
          handlers.fail({ type: 'api', code: code })
        }
        return
      }
      if (handlers.success) {
        handlers.success(snapshot, code)
      }
    },
    fail: (data, code) => {
      if (handlers.fail) {
        handlers.fail(networkError(data, code))
      }
    },
    complete: () => {
      if (handlers.complete) {
        handlers.complete()
      }
    }
  })
  return true
}

// Lightweight request for the on-watch diagnostics page. No custom headers are
// sent, so this separates basic connectivity from API authentication failures.
export function probeUrl(url, callbacks) {
  const handlers = callbacks || {}
  fetch.fetch({
    url: url,
    method: 'GET',
    responseType: 'text',
    success: (response) => {
      const code = Number(response && response.code)
      if (code >= 200 && code < 400) {
        if (handlers.success) {
          handlers.success(code)
        }
      } else if (handlers.fail) {
        handlers.fail({ type: 'http', code: isNaN(code) ? 0 : code })
      }
    },
    fail: (data, code) => {
      if (handlers.fail) {
        handlers.fail(networkError(data, code))
      }
    },
    complete: () => {
      if (handlers.complete) {
        handlers.complete()
      }
    }
  })
}
