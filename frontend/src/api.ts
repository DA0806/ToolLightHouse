import type {
  AnalysisResponse,
  ArduinoLedTestResponse,
  ArduinoMaintenanceResponse,
  ArduinoStatus,
  BackendCameraSourceDescriptor,
  CalibrationMeta,
  CalibrationProfile,
  CameraInviteResponse,
  CameraSourcesResponse,
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
  CameraSourceDescriptor,
} from './types'

export interface Credentials {
  sessionId: string
  ticket: string
  role: 'controller' | 'reader' | 'admin'
  wsTicket?: string
}

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status = 0, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export function parseErrorBody(parsed: unknown, status: number): { message: string; code?: string } {
  let message = `HTTP ${status}`
  let code: string | undefined = undefined
  if (typeof parsed === 'object' && parsed !== null) {
    const obj = parsed as Record<string, unknown>
    if (typeof obj.code === 'string') {
      code = obj.code
    }
    if (typeof obj.detail === 'string') {
      message = obj.detail
    } else if (typeof obj.detail === 'object' && obj.detail !== null) {
      const detailObj = obj.detail as Record<string, unknown>
      if (typeof detailObj.code === 'string') {
        code = detailObj.code
      }
      if (typeof detailObj.message === 'string') {
        message = detailObj.message
      } else if (typeof detailObj.detail === 'string') {
        message = detailObj.detail
      } else {
        try {
          message = JSON.stringify(obj.detail)
        } catch {
          message = String(obj.detail)
        }
      }
    } else if (typeof obj.message === 'string') {
      message = obj.message
    }
  } else if (typeof parsed === 'string' && parsed.trim().length > 0) {
    message = parsed
  }
  return { message, code }
}

export function normalizeApiUrl(url: string): string {
  return url.trim().replace(/\/+$/, '')
}

export function websocketUrl(baseUrl: string, path: string): string {
  const parsed = new URL(normalizeApiUrl(baseUrl))
  parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:'
  parsed.pathname = path.startsWith('/') ? path : `/${path}`
  parsed.search = ''
  parsed.hash = ''
  return parsed.toString()
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
      const { message, code } = parseErrorBody(parsed, response.status)
      throw new ApiError(message, response.status, code)
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

export function streamFrame(
  baseUrl: string,
  credentials: Credentials,
  file?: Blob,
  timeoutMs = 12000,
  sourceContext?: { sourceId?: string; sourceVersion?: number; streamRun?: number },
): Promise<{ accepted: boolean; frame_id: string; latest_only: boolean; result?: AnalysisResponse }> {
  const body = new FormData()
  if (file) {
    body.append('file', file, file instanceof File ? file.name : 'stream.jpg')
  }
  const headers = new Headers(authHeaders(credentials))
  if (sourceContext?.sourceId) headers.set('X-Source-Id', sourceContext.sourceId)
  if (sourceContext?.sourceVersion !== undefined) headers.set('X-Source-Version', String(sourceContext.sourceVersion))
  if (sourceContext?.streamRun !== undefined) headers.set('X-Stream-Run', String(sourceContext.streamRun))
  return request(`${normalizeApiUrl(baseUrl)}/v1/stream/frame`, { method: 'POST', headers, body }, timeoutMs)
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
  const parsed = new URL(websocketUrl(baseUrl, '/v1/ws'))
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

// Camera Management & Five-Source Integration
function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? value as Record<string, unknown> : null
}

export function decodeCameraSourceDescriptor(value: unknown): CameraSourceDescriptor {
  const raw = asRecord(value)
  if (!raw || typeof raw.source_id !== 'string' || typeof raw.display_name !== 'string') {
    throw new ApiError('La respuesta de fuentes de cámara no tiene un identificador o nombre válido')
  }
  const canonicalSourceIds = new Set(['remote_phone', 'laptop_builtin', 'usb_webcam', 'operator_device', 'manual_upload'])
  if (!canonicalSourceIds.has(raw.source_id)) {
    throw new ApiError(`Fuente de cámara no soportada: ${raw.source_id}`)
  }
  const resolution = asRecord(raw.current_resolution)
  const width = Number(resolution?.width)
  const height = Number(resolution?.height)
  const fps = Number(raw.current_fps)
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0 || !Number.isFinite(fps) || fps < 0) {
    throw new ApiError(`La fuente de cámara ${raw.source_id} no tiene resolución o FPS válidos`)
  }
  const kindByBackend: Record<string, CameraSourceDescriptor['kind']> = {
    webrtc_remote: 'webrtc',
    opencv_builtin: 'opencv',
    opencv_usb: 'opencv',
    browser_local: 'browser',
    manual_upload: 'file',
  }
  const kind = kindByBackend[String(raw.kind)]
  if (!kind) throw new ApiError(`Tipo de fuente de cámara no soportado: ${String(raw.kind)}`)
  const status = String(raw.status)
  const validStatuses = new Set(['active', 'standby', 'disconnected', 'error', 'needs_calibration'])
  if (!validStatuses.has(status)) throw new ApiError(`Estado de fuente de cámara no soportado: ${status}`)
  if (typeof raw.calibration_profile_available !== 'boolean') {
    throw new ApiError(`La fuente de cámara ${raw.source_id} no informa disponibilidad de calibración`)
  }
  return {
    source_id: raw.source_id,
    name: raw.display_name,
    kind,
    status: status as CameraSourceDescriptor['status'],
    resolution: { width, height },
    fps,
    requires_operator_action: raw.connection_type === 'browser' || raw.connection_type === 'file',
    calibration_available: raw.calibration_profile_available,
    connection_type: typeof raw.connection_type === 'string' ? raw.connection_type : undefined,
    is_active: raw.is_active === true,
    last_error: typeof raw.last_error === 'string' ? raw.last_error : null,
  }
}

export function decodeCameraSourcesResponse(value: unknown): CameraSourcesResponse {
  const raw = asRecord(value)
  if (!raw || !Array.isArray(raw.sources) || typeof raw.active_source_id !== 'string' || !Number.isFinite(Number(raw.source_version))) {
    throw new ApiError('La respuesta de fuentes de cámara no tiene el contrato esperado')
  }
  return {
    sources: raw.sources.map(decodeCameraSourceDescriptor),
    active_source_id: raw.active_source_id,
    source_version: Number(raw.source_version),
  }
}

export async function getCameraSources(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<CameraSourcesResponse> {
  const raw = await request<BackendCameraSourceDescriptor[] | CameraSourcesResponse>(`${normalizeApiUrl(baseUrl)}/v1/camera/sources`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
  return decodeCameraSourcesResponse(raw)
}

export function setActiveCameraSource(baseUrl: string, credentials: Credentials, sourceId: string, timeoutMs = 8000): Promise<{ success: boolean; active_source_id: string; source_version: number; calibration_status?: string }> {
  return request<{ success: boolean; active_source_id: string; source_version: number; calibration_status?: string }>(`${normalizeApiUrl(baseUrl)}/v1/camera/sources/active`, {
    method: 'POST',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId }),
  }, timeoutMs)
}

export interface WebCamDevice {
  index: number
  name: string
  width?: number
  height?: number
  fps?: number
  is_active: boolean
}

export function getWebcams(baseUrl: string, credentials: Credentials, timeoutMs = 8000): Promise<{ devices: WebCamDevice[] }> {
  return request<{ devices: WebCamDevice[] }>(`${normalizeApiUrl(baseUrl)}/v1/camera/webcams`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function updateCameraSourceSettings(
  baseUrl: string,
  credentials: Credentials,
  sourceId: string,
  settings: { device_index?: number; width?: number; height?: number; fps?: number },
  timeoutMs = 8000
): Promise<{ success: boolean; profile_status: string }> {
  return request<{ success: boolean; profile_status: string }>(`${normalizeApiUrl(baseUrl)}/v1/camera/sources/${encodeURIComponent(sourceId)}/settings`, {
    method: 'POST',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  }, timeoutMs)
}

export function createCameraInvite(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<CameraInviteResponse> {
  return request<CameraInviteResponse>(`${normalizeApiUrl(baseUrl)}/v1/camera/invite`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function cancelCameraInvite(baseUrl: string, credentials: Credentials, inviteId: string, timeoutMs = 5000): Promise<{ cancelled: boolean }> {
  return request<{ cancelled: boolean }>(`${normalizeApiUrl(baseUrl)}/v1/camera/invite/${encodeURIComponent(inviteId)}`, {
    method: 'DELETE',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function revokeCameraTransmitters(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ revoked: boolean; disconnected_transmitters: number }> {
  return request<{ revoked: boolean; disconnected_transmitters: number }>(`${normalizeApiUrl(baseUrl)}/v1/camera/revoke`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export async function scanTools(baseUrl: string, credentials: Credentials, file?: File | Blob, timeoutMs = 15000): Promise<AnalysisResponse> {
  const formData = new FormData()
  if (file) {
    formData.append('file', file, 'scan.jpg')
  }
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${normalizeApiUrl(baseUrl)}/v1/analyze/scan`, {
      method: 'POST',
      headers: authHeaders(credentials),
      body: file ? formData : undefined,
      signal: controller.signal,
    })
    const body = await response.text()
    let parsed: unknown = null
    try { parsed = body ? JSON.parse(body) : null } catch { parsed = body }
    if (!response.ok) {
      const { message, code } = parseErrorBody(parsed, response.status)
      throw new ApiError(message, response.status, code)
    }
    return parsed as AnalysisResponse
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError('Tiempo de espera agotado al escanear', 408)
    if (error instanceof ApiError) throw error
    throw new ApiError('No se pudo contactar al backend')
  } finally {
    window.clearTimeout(timeout)
  }
}

export function takeoverLease(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ success: boolean; lease_generation: number; holder: string }> {
  return request<{ success: boolean; lease_generation: number; holder: string }>(`${normalizeApiUrl(baseUrl)}/v1/sessions/lease/takeover`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export function sessionHeartbeat(
  baseUrl: string,
  credentials: Credentials,
  timeoutMs = 5000,
): Promise<{ success: boolean; alive: boolean; lease_held: boolean; lease_generation: number; expires_in: number }> {
  return request<{ success: boolean; alive: boolean; lease_held: boolean; lease_generation: number; expires_in: number }>(
    `${normalizeApiUrl(baseUrl)}/v1/sessions/heartbeat`,
    {
      method: 'POST',
      headers: authHeaders(credentials),
    },
    timeoutMs,
  )
}

export function stopStream(
  baseUrl: string,
  credentials: Credentials,
  timeoutMs = 5000,
  sourceContext?: { sourceId?: string; sourceVersion?: number; streamRun?: number },
): Promise<{ stopped: boolean; stream_generation: number }> {
  const headers = new Headers(authHeaders(credentials))
  if (sourceContext?.sourceId) headers.set('X-Source-Id', sourceContext.sourceId)
  if (sourceContext?.sourceVersion !== undefined) headers.set('X-Source-Version', String(sourceContext.sourceVersion))
  if (sourceContext?.streamRun !== undefined) headers.set('X-Stream-Run', String(sourceContext.streamRun))
  return request<{ stopped: boolean; stream_generation: number }>(
    `${normalizeApiUrl(baseUrl)}/v1/stream/stop`,
    {
      method: 'POST', headers,
    },
    timeoutMs,
  )
}

export function getCalibrationProfiles(baseUrl: string, credentials: Credentials, timeoutMs = 5000): Promise<{ profiles: Record<string, CalibrationProfile> }> {
  return request<{ profiles: Record<string, CalibrationProfile> }>(`${normalizeApiUrl(baseUrl)}/v1/calibration/profiles`, {
    headers: authHeaders(credentials),
  }, timeoutMs)
}

export async function captureProfileBackground(baseUrl: string, credentials: Credentials, sourceId: string, file?: File | Blob, timeoutMs = 15000): Promise<CalibrationProfile> {
  const formData = new FormData()
  if (file) {
    formData.append('file', file, 'bg.jpg')
  }
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${normalizeApiUrl(baseUrl)}/v1/calibration/profiles/${encodeURIComponent(sourceId)}/capture`, {
      method: 'POST',
      headers: authHeaders(credentials),
      body: file ? formData : undefined,
      signal: controller.signal,
    })
    const body = await response.text()
    let parsed: unknown = null
    try { parsed = body ? JSON.parse(body) : null } catch { parsed = body }
    if (!response.ok) {
      const { message, code } = parseErrorBody(parsed, response.status)
      throw new ApiError(message, response.status, code)
    }
    return parsed as CalibrationProfile
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError('Tiempo de espera agotado al capturar calibración', 408)
    if (error instanceof ApiError) throw error
    throw new ApiError('No se pudo contactar al backend')
  } finally {
    window.clearTimeout(timeout)
  }
}

export function updateProfileRoi(baseUrl: string, credentials: Credentials, sourceId: string, roi: [number, number, number, number], timeoutMs = 5000): Promise<CalibrationProfile> {
  return request<CalibrationProfile>(`${normalizeApiUrl(baseUrl)}/v1/calibration/profiles/${encodeURIComponent(sourceId)}/roi`, {
    method: 'PUT',
    headers: { ...authHeaders(credentials), 'Content-Type': 'application/json' },
    body: JSON.stringify({ roi }),
  }, timeoutMs)
}

export function confirmLegacyProfile(baseUrl: string, credentials: Credentials, sourceId: string, timeoutMs = 5000): Promise<CalibrationProfile> {
  return request<CalibrationProfile>(`${normalizeApiUrl(baseUrl)}/v1/calibration/profiles/${encodeURIComponent(sourceId)}/confirm-legacy`, {
    method: 'POST',
    headers: authHeaders(credentials),
  }, timeoutMs)
}
