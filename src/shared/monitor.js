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
    responseType: 'json',
    success: (response) => {
      const code = Number(response && response.code)
      const snapshot = parseSnapshot(response && response.data)
      if (code < 200 || code >= 300 || !snapshot || !snapshot.ok) {
        if (handlers.fail) {
          handlers.fail()
        }
        return
      }
      if (handlers.success) {
        handlers.success(snapshot)
      }
    },
    fail: () => {
      if (handlers.fail) {
        handlers.fail()
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
