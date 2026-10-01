'use strict'

const JSON_HEADERS = {
  'content-type': 'application/json; charset=utf-8',
  'cache-control': 'no-store'
}

function response(statusCode, body) {
  return {
    statusCode: statusCode,
    headers: JSON_HEADERS,
    isBase64Encoded: false,
    body: JSON.stringify(body)
  }
}

function parseEvent(event) {
  if (Buffer.isBuffer(event)) {
    return JSON.parse(event.toString('utf8'))
  }
  if (typeof event === 'string') {
    return JSON.parse(event)
  }
  return event || {}
}

function getHeader(headers, name) {
  const source = headers || {}
  const expected = String(name).toLowerCase()
  const key = Object.keys(source).find((item) => String(item).toLowerCase() === expected)
  return key ? String(source[key]) : ''
}

exports.handler = async function(event) {
  let request
  try {
    request = parseEvent(event)
  } catch (error) {
    return response(400, { ok: false, error: 'invalid request' })
  }

  const path = request.rawPath ||
    (request.requestContext && request.requestContext.http && request.requestContext.http.path) || '/'
  const method = String(
    request.requestContext && request.requestContext.http && request.requestContext.http.method || 'GET'
  ).toUpperCase()

  if (method === 'GET' && (path === '/' || path === '/health')) {
    return response(200, { ok: true, relay: 'aliyun-fc' })
  }
  if (method !== 'GET' || path !== '/api/status') {
    return response(404, { ok: false, error: 'not found' })
  }

  const expectedToken = process.env.WATCH_TOKEN || ''
  const providedToken = getHeader(request.headers, 'x-codex-watch-token')
  if (!expectedToken || providedToken !== expectedToken) {
    return response(401, { ok: false, error: 'unauthorized' })
  }

  const upstreamUrl = process.env.UPSTREAM_URL || ''
  if (!upstreamUrl.startsWith('https://')) {
    return response(500, { ok: false, error: 'relay is not configured' })
  }

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    const upstream = await fetch(upstreamUrl, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        'X-Codex-Watch-Token': expectedToken
      },
      signal: controller.signal
    })
    const body = await upstream.text()
    return {
      statusCode: upstream.status,
      headers: JSON_HEADERS,
      isBase64Encoded: false,
      body: body
    }
  } catch (error) {
    return response(502, { ok: false, error: 'upstream unavailable' })
  } finally {
    clearTimeout(timeout)
  }
}
