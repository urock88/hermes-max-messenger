// MAX sessions for Hermes Desktop: a page listing conversations that came from the MAX messenger,
// a sidebar entry, a ⌘K command and a small "MAX" mark on those rows in the session list.
//
// Loaded uncompiled (no JSX). The SDK is imported as a namespace and every member is checked before
// use, so a renamed or missing export degrades one feature instead of failing the whole plugin.
// session.list parameters are the documented ones ({ limit }); on a refusal the call is retried
// without parameters, because the RPC contract has changed between releases.

import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'max-messenger'
const ROUTE = '/max-sessions'
const SOURCE = 'max'
const REFRESH_MS = 30000

const ru = (navigator.language || '').toLowerCase().startsWith('ru')
const T = ru
  ? { title: 'Сессии MAX', empty: 'В MAX ещё не было разговоров', refresh: 'Обновить', open: 'Открыть',
      messages: 'сообщ.', failed: 'Не удалось загрузить сессии', palette: 'Открыть сессии MAX' }
  : { title: 'MAX sessions', empty: 'No MAX conversations yet', refresh: 'Refresh', open: 'Open',
      messages: 'msgs', failed: 'Could not load sessions', palette: 'Open MAX sessions' }

// Ids of MAX sessions (both the live id and the durable lineage root), shared with the row marks.
let maxIds = new Set()

async function loadSessions() {
  const request = sdk.host && sdk.host.request
  if (typeof request !== 'function') throw new Error('host.request is not available')
  let result
  try {
    result = await request('session.list', { limit: 300 })
  } catch (_first) {
    result = await request('session.list')
  }
  const rows = (result && Array.isArray(result.sessions) ? result.sessions : [])
    .filter(row => String(row.source || '').toLowerCase() === SOURCE)
  maxIds = new Set(rows.flatMap(row => [row.id, row._lineage_root_id].filter(Boolean)))
  return rows
}

function when(startedAt) {
  if (!startedAt) return ''
  const ms = startedAt > 1e12 ? startedAt : startedAt * 1000
  if (typeof sdk.relativeTime === 'function') {
    try { return sdk.relativeTime(ms) } catch (_e) { /* fall through */ }
  }
  return new Date(ms).toLocaleString()
}

function openSession(row) {
  const id = row._lineage_root_id || row.id
  if (sdk.host && typeof sdk.host.openSession === 'function') sdk.host.openSession(id)
}

function Row({ row }) {
  return jsxs('button', {
    type: 'button',
    onClick: () => openSession(row),
    className: 'flex w-full flex-col gap-0.5 rounded-md px-3 py-2 text-left hover:bg-(--ui-bg-hover)',
    children: [
      jsxs('div', {
        className: 'flex items-baseline gap-2',
        children: [
          jsx('span', { className: 'truncate font-medium text-(--ui-text-primary)', children: row.title || row.preview || row.id }),
          jsx('span', { className: 'ml-auto shrink-0 text-xs text-(--ui-text-tertiary)', children: when(row.started_at) })
        ]
      }),
      jsxs('div', {
        className: 'flex gap-2 text-xs text-(--ui-text-tertiary)',
        children: [
          jsx('span', { className: 'truncate', children: row.title ? row.preview : '' }),
          jsx('span', { className: 'ml-auto shrink-0', children: `${row.message_count || 0} ${T.messages}` })
        ]
      })
    ]
  })
}

function MaxSessionsPage({ ctx }) {
  const [state, setState] = useState({ rows: null, error: null })

  useEffect(() => {
    let alive = true
    const refresh = () => loadSessions()
      .then(rows => alive && setState({ rows, error: null }))
      .catch(error => alive && setState(s => ({ rows: s.rows, error: String(error && error.message || error) })))
    refresh()
    const stop = ctx.setInterval(refresh, REFRESH_MS)
    return () => { alive = false; stop() }
  }, [])

  const { rows, error } = state
  const Button = sdk.Button
  const refreshButton = Button
    ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => loadSessions().then(r => setState({ rows: r, error: null })), children: T.refresh })
    : null
  let body
  if (rows === null && !error) body = jsx('div', { className: 'p-4 text-sm text-(--ui-text-tertiary)', children: '…' })
  else if (error && !rows) body = jsx('div', { className: 'p-4 text-sm text-(--ui-text-danger)', children: `${T.failed}: ${error}` })
  else if (!rows.length) body = jsx('div', { className: 'p-4 text-sm text-(--ui-text-tertiary)', children: T.empty })
  else body = jsx('div', { className: 'flex flex-col gap-0.5 p-2', children: rows.map(row => jsx(Row, { row }, row.id)) })

  return jsxs('div', {
    className: 'flex h-full flex-col overflow-auto',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 border-b border-(--ui-border) px-4 py-3',
        children: [
          jsx('span', { className: 'rounded bg-(--ui-accent-soft) px-1.5 py-0.5 text-xs font-semibold text-(--ui-accent)', children: 'MAX' }),
          jsx('span', { className: 'font-medium text-(--ui-text-primary)', children: T.title }),
          jsx('span', { className: 'ml-auto', children: refreshButton })
        ]
      }),
      body
    ]
  })
}

export default {
  id: ID,
  name: 'MAX messenger',
  register(ctx) {
    const contributions = []
    if (sdk.ROUTES_AREA) {
      contributions.push({ id: 'page', area: sdk.ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(MaxSessionsPage, { ctx }) })
    }
    if (sdk.SIDEBAR_NAV_AREA) {
      contributions.push({ id: 'nav', area: sdk.SIDEBAR_NAV_AREA, data: { path: ROUTE, label: T.title, codicon: 'comment-discussion' } })
    }
    if (sdk.PALETTE_AREA) {
      contributions.push({
        id: 'open', area: sdk.PALETTE_AREA,
        data: { id: 'open', label: T.palette, keywords: ['max', 'messenger', 'макс'], run: () => sdk.host.navigate(ROUTE) }
      })
    }
    if (sdk.SESSION_ROW_AREAS && sdk.SESSION_ROW_AREAS.trailing) {
      contributions.push({
        id: 'row-mark', area: sdk.SESSION_ROW_AREAS.trailing,
        data: {
          render: ({ sessionId }) => maxIds.has(sessionId)
            ? jsx('span', { title: 'MAX', className: 'rounded px-1 text-[0.625rem] font-semibold text-(--ui-accent)', children: 'MAX' })
            : null
        }
      })
    }
    ctx.registerMany(contributions)
    // Fill the row marks without waiting for the page to be opened.
    loadSessions().catch(() => {})
    ctx.setInterval(() => { loadSessions().catch(() => {}) }, REFRESH_MS * 2)
  }
}
