import type { AnalysisResponse, InventoryResponse, SessionResponse, StatusResponse, WsEvent } from './types'

export interface Credentials {
  sessionId: string
  ticket: string
  role: 'controller' | 'reader'
  wsTicket?: string
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function normalizeApiUrl(url: string): string {
  return url.trim().replace(/\/+$/, '')
}

export function defaultApiUrl(): string {
  if (import.meta.env.VITE_API_URL) return normalizeApiUrl(import.meta.env.VITE_API_URL)
  const current = new URL(window.location.href)
  if (current.port === '5173' || current.port === '4173') current.port = '8000'
  return normalizeApiUrl(current.origin)
}

async function request<T>(url: string, init: RequestInit, timeoutMs: number): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(url, { ...init, signal: controller.signal })
    const body = await response.text()
    let parsed: unknown = null
    try { parsed = body ? JSON.parse(body) : null } catch { parsed = body }
    if (!response.ok) {
      const detail = typeof parsed === 'object' && parsed && 'detail' in parsed
        ? String((parsed as { detail: unknown }).detail)
        : `HTTP ${response.status}`
      throw new ApiError(detail, response.status)
    }
    return parsed as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError('Tiempo de espera agotado', 408)
    if (error instanceof ApiError) throw error
    throw new ApiError('No se pudo contactar al backend')
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function createSession(baseUrl: string, pairingKey: string, deviceId: string, role: 'controller' | 'reader', timeoutMs = 8000): Promise<SessionResponse> {
  return request<SessionResponse>(`${normalizeApiUrl(baseUrl)}/v1/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Pairing-Key': pairingKey },
    body: JSON.stringify({ device_id: deviceId, role }),
  }, timeoutMs)
}

function authHeaders(credentials: Credentials): HeadersInit {
  return { 'X-Session-Id': credentials.sessionId, 'X-Session-Ticket': credentials.ticket }
}

export function getStatus(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<StatusResponse> {
  return request<StatusResponse>(`${normalizeApiUrl(baseUrl)}/v1/status`, { headers: authHeaders(credentials) }, timeoutMs)
}

export async function createWsTicket(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ ticket: string; expires_at?: string }> {
  return request(`${normalizeApiUrl(baseUrl)}/v1/sessions/${encodeURIComponent(credentials.sessionId)}/ws-ticket`, {
    method: 'POST', headers: { 'X-Session-Ticket': credentials.ticket },
  }, timeoutMs)
}

export function getInventory(baseUrl: string, timeoutMs = 5000): Promise<InventoryResponse> {
  return request<InventoryResponse>(`${normalizeApiUrl(baseUrl)}/v1/inventory`, {}, timeoutMs)
}

export function analyzePhoto(baseUrl: string, credentials: Credentials, file: Blob, timeoutMs = 30000): Promise<AnalysisResponse> {
  const body = new FormData()
  body.append('file', file, file instanceof File ? file.name : 'capture.jpg')
  return request<AnalysisResponse>(`${normalizeApiUrl(baseUrl)}/v1/analyze/photo`, {
    method: 'POST', headers: authHeaders(credentials), body,
  }, timeoutMs)
}

export function streamFrame(baseUrl: string, credentials: Credentials, file: Blob, timeoutMs = 12000): Promise<{ accepted: boolean; frame_id: string; latest_only: boolean; result?: AnalysisResponse }> {
  const body = new FormData()
  body.append('file', file, file instanceof File ? file.name : 'stream.jpg')
  return request(`${normalizeApiUrl(baseUrl)}/v1/stream/frame`, { method: 'POST', headers: authHeaders(credentials), body }, timeoutMs)
}

export async function releaseSession(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<void> {
  await request(`${normalizeApiUrl(baseUrl)}/v1/sessions/${encodeURIComponent(credentials.sessionId)}`, {
    method: 'DELETE', headers: { 'X-Session-Ticket': credentials.ticket },
  }, timeoutMs)
}

export function calibrateBackground(baseUrl: string, credentials: Credentials, file: Blob, timeoutMs = 30000): Promise<Record<string, unknown>> {
  const body = new FormData()
  body.append('file', file, file instanceof File ? file.name : 'background.jpg')
  return request(`${normalizeApiUrl(baseUrl)}/v1/calibration/background`, {
    method: 'POST', headers: authHeaders(credentials), body,
  }, timeoutMs)
}

export function openEvents(baseUrl: string, credentials: Credentials, onEvent: (event: WsEvent) => void, onState: (state: 'open' | 'closed' | 'error') => void): { close: () => void } {
  const parsed = new URL(normalizeApiUrl(baseUrl))
  parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:'
  parsed.pathname = `${parsed.pathname.replace(/\/$/, '')}/v1/ws`
  if (!credentials.wsTicket) throw new ApiError('No se obtuvo un ticket efímero para WebSocket')
  parsed.searchParams.set('ticket', credentials.wsTicket)
  parsed.searchParams.set('session_id', credentials.sessionId)
  const socket = new WebSocket(parsed.toString())
  socket.onopen = () => onState('open')
  socket.onclose = () => onState('closed')
  socket.onerror = () => onState('error')
  socket.onmessage = (message) => {
    try { onEvent(JSON.parse(message.data as string) as WsEvent) } catch { /* ignore malformed event */ }
  }
  return { close: () => socket.close(1000, 'client closed') }
}
