import type {
  AnalysisResponse,
  ArduinoLedTestResponse,
  ArduinoMaintenanceResponse,
  ArduinoStatus,
  CalibrationMeta,
  ComPort,
  ConfigResponse,
  ConfigStatusResponse,
  InventoryConfig,
  InventoryResponse,
  ReferenceListResponse,
  ReferencePhoto,
  SessionResponse,
  SettingsConfig,
  StatusResponse,
  SystemReadiness,
  WsEvent,
} from './types'

export interface Credentials {
  sessionId: string
  ticket: string
  role: 'controller' | 'reader' | 'admin'
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

export async function createSession(
  baseUrl: string,
  key: string,
  deviceId: string,
  role: 'controller' | 'reader' | 'admin',
  timeoutMs = 8000
): Promise<SessionResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (role === 'admin') {
    if (key) headers['X-Admin-Key'] = key
  } else {
    if (key) headers['X-Pairing-Key'] = key
  }
  return request<SessionResponse>(
    `${normalizeApiUrl(baseUrl)}/v1/sessions`,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({ device_id: deviceId, role }),
    },
    timeoutMs
  )
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

// Config endpoints
export function getConfig(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<ConfigResponse> {
  return request<ConfigResponse>(`${normalizeApiUrl(baseUrl)}/v1/config`, { headers: authHeaders(credentials) }, timeoutMs)
}

export function getConfigStatus(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<ConfigStatusResponse> {
  return request<ConfigStatusResponse>(`${normalizeApiUrl(baseUrl)}/v1/config/status`, { headers: authHeaders(credentials) }, timeoutMs)
}

export function updateConfig(
  baseUrl: string,
  credentials: Credentials,
  payload: { expected_version?: number; settings: Partial<SettingsConfig>; inventory: InventoryConfig },
  timeoutMs = 8000
): Promise<ConfigResponse> {
  return request<ConfigResponse>(`${normalizeApiUrl(baseUrl)}/v1/config`, {
    method: 'PUT',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, timeoutMs)
}

export function applyConfig(baseUrl: string, credentials: Credentials, timeoutMs = 45000): Promise<ConfigResponse> {
  return request<ConfigResponse>(`${normalizeApiUrl(baseUrl)}/v1/config/apply`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function rollbackConfig(baseUrl: string, credentials: Credentials, timeoutMs = 8000): Promise<ConfigResponse> {
  return request<ConfigResponse>(`${normalizeApiUrl(baseUrl)}/v1/config/rollback`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function exportConfig(baseUrl: string, credentials: Credentials, timeoutMs = 8000): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`${normalizeApiUrl(baseUrl)}/v1/config/export`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function importConfig(baseUrl: string, credentials: Credentials, payload: Record<string, unknown>, timeoutMs = 8000): Promise<ConfigResponse> {
  return request<ConfigResponse>(`${normalizeApiUrl(baseUrl)}/v1/config/import`, {
    method: 'POST',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, timeoutMs)
}

// System endpoints
export function getReadiness(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<SystemReadiness> {
  return request<SystemReadiness>(`${normalizeApiUrl(baseUrl)}/v1/system/readiness`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function setSystemMode(baseUrl: string, credentials: Credentials, mode: 'mock' | 'preparation' | 'real', timeoutMs = 15000): Promise<{ mode: string; readiness: SystemReadiness }> {
  return request<{ mode: string; readiness: SystemReadiness }>(`${normalizeApiUrl(baseUrl)}/v1/system/mode`, {
    method: 'POST',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  }, timeoutMs)
}

export function downloadModelInstructions(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ status: string; detail: string; path?: string }> {
  return request<{ status: string; detail: string; path?: string }>(`${normalizeApiUrl(baseUrl)}/v1/system/download-model`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

// Visual References
export function listReferences(baseUrl: string, credentials: Credentials, classId: string, timeoutMs = 5000): Promise<ReferenceListResponse> {
  return request<ReferenceListResponse>(`${normalizeApiUrl(baseUrl)}/v1/tools/${encodeURIComponent(classId)}/references`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function uploadReference(baseUrl: string, credentials: Credentials, classId: string, file: Blob, timeoutMs = 30000): Promise<ReferencePhoto> {
  const body = new FormData()
  body.append('file', file, file instanceof File ? file.name : 'reference.jpg')
  return request<ReferencePhoto>(`${normalizeApiUrl(baseUrl)}/v1/tools/${encodeURIComponent(classId)}/references`, {
    method: 'POST',
    headers: authHeaders(credentials),
    body,
  }, timeoutMs)
}

export function deleteReference(baseUrl: string, credentials: Credentials, classId: string, imageId: string, timeoutMs = 5000): Promise<{ deleted: boolean; image_id: string; class_id: string }> {
  return request<{ deleted: boolean; image_id: string; class_id: string }>(`${normalizeApiUrl(baseUrl)}/v1/tools/${encodeURIComponent(classId)}/references/${encodeURIComponent(imageId)}`, {
    method: 'DELETE',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

// Calibration & ROI
export function getCalibration(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<CalibrationMeta> {
  return request<CalibrationMeta>(`${normalizeApiUrl(baseUrl)}/v1/calibration`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export async function getCalibrationBackgroundBlob(baseUrl: string, credentials: Credentials, timeoutMs = 15000): Promise<Blob> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${normalizeApiUrl(baseUrl)}/v1/calibration/background.jpg`, {
      headers: authHeaders(credentials),
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new ApiError(`Error al descargar fondo: HTTP ${response.status}`, response.status)
    }
    return await response.blob()
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError('Tiempo de espera agotado al descargar fondo', 408)
    if (error instanceof ApiError) throw error
    throw new ApiError('No se pudo descargar la imagen de fondo')
  } finally {
    window.clearTimeout(timeout)
  }
}

export function updateRoi(baseUrl: string, credentials: Credentials, roi: [number, number, number, number], timeoutMs = 5000): Promise<{ calibrated: boolean; roi: [number, number, number, number] }> {
  return request<{ calibrated: boolean; roi: [number, number, number, number] }>(`${normalizeApiUrl(baseUrl)}/v1/calibration/roi`, {
    method: 'PUT',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify({ roi }),
  }, timeoutMs)
}

// Arduino Hardware & Maintenance
export function getArduinoPorts(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ ports: ComPort[] }> {
  return request<{ ports: ComPort[] }>(`${normalizeApiUrl(baseUrl)}/v1/arduino/ports`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function connectArduino(baseUrl: string, credentials: Credentials, payload: { port?: string; simulated?: boolean }, timeoutMs = 8000): Promise<{ connected: boolean; arduino: ArduinoStatus }> {
  return request<{ connected: boolean; arduino: ArduinoStatus }>(`${normalizeApiUrl(baseUrl)}/v1/arduino/connect`, {
    method: 'POST',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, timeoutMs)
}

export function disconnectArduino(baseUrl: string, credentials: Credentials, timeoutMs = 8000): Promise<{ arduino: ArduinoStatus }> {
  return request<{ arduino: ArduinoStatus }>(`${normalizeApiUrl(baseUrl)}/v1/arduino/disconnect`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function enterMaintenance(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<ArduinoMaintenanceResponse> {
  return request<ArduinoMaintenanceResponse>(`${normalizeApiUrl(baseUrl)}/v1/arduino/maintenance/enter`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function leaveMaintenance(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<ArduinoMaintenanceResponse> {
  return request<ArduinoMaintenanceResponse>(`${normalizeApiUrl(baseUrl)}/v1/arduino/maintenance/leave`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function testLed(baseUrl: string, credentials: Credentials, idx: number, duration = 2.0, timeoutMs = 5000): Promise<ArduinoLedTestResponse> {
  return request<ArduinoLedTestResponse>(`${normalizeApiUrl(baseUrl)}/v1/arduino/maintenance/test-led/${idx}?duration=${encodeURIComponent(duration)}`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function allOff(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ arduino: ArduinoStatus }> {
  return request<{ arduino: ArduinoStatus }>(`${normalizeApiUrl(baseUrl)}/v1/arduino/maintenance/all-off`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}
