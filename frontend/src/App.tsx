import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  analyzePhoto,
  ApiError,
  applyConfig,
  calibrateBackground,
  cancelCameraInvite,
  createCameraInvite,
  createSession,
  createWsTicket,
  defaultApiUrl,
  getCameraSources,
  getConfig,
  getConfigStatus,
  getInventory,
  getStatus,
  getWebcams,
  openEvents,
  releaseSession,
  revokeCameraTransmitters,
  scanTools,
  setActiveCameraSource,
  sessionHeartbeat,
  stopStream,
  streamFrame,
  takeoverLease,
  updateConfig,
  updateCameraSourceSettings,
  type Credentials,
  type WebCamDevice,
} from './api'
import type {
  ActiveTab,
  ActivityLogItem,
  AnalysisResponse,
  CameraInviteResponse,
  CameraSourceDescriptor,
  ConfigResponse,
  ConfigStatusResponse,
  ConnectionState,
  Detection,
  InventoryItem,
  StatusResponse,
  SettingsConfig,
  WsEvent,
} from './types'
import { Navigation } from './components/Navigation'
import { ToolManager } from './components/ToolManager'
import { CalibrationView } from './components/CalibrationView'
import { ArduinoPanel } from './components/ArduinoPanel'
import { SystemPanel } from './components/SystemPanel'
import { CameraSelector } from './components/CameraSelector'
import { createVideoFreshnessState, waitForFreshVideoFrame, type VideoFreshnessState } from './videoFreshness'
import { QRModal } from './components/QRModal'
import { RemoteCameraView } from './components/RemoteCameraView'
import './styles.css'

const DEVICE_KEY = 'toollighthouse.device-id'
const URL_KEY = 'toollighthouse.api-url'
const MAX_RECONNECT_ATTEMPTS = 5

export function isTrueAuthorityLoss(status: number, message?: string, code?: string): boolean {
  if (status !== 409) return false
  if (code) {
    const normCode = code.toLowerCase().trim()
    if (['lease_lost', 'lease_expired', 'controller_conflict', 'authority_loss'].includes(normCode)) {
      return true
    }
  }
  if (!message) return false
  const lower = message.toLowerCase().trim()
  const exactPatterns = [
    'controller lease expired',
    'another controller session is active',
    'controller lease expired during inference',
    'physical control lease required for maintenance operations',
    'current active physical control lease required to alter camera settings',
    'physical control lease lost',
    'lease revoked',
  ]
  return exactPatterns.some((pattern) => lower.includes(pattern))
}

export function mapActionableErrorMessage(error: unknown): string {
  if (!error) return 'Error desconocido'
  if (typeof error === 'string') return error
  const status = error instanceof ApiError ? error.status : (error as { status?: number }).status
  const message = error instanceof Error ? error.message : String(error)
  const code = error instanceof ApiError ? error.code : (error as { code?: string }).code
  const lower = message.toLowerCase()
  const lowerCode = code?.toLowerCase() || ''

  if (status === 401) {
    return 'La sesión expiró o es inválida. Empareja de nuevo.'
  }

  if (status === 403) {
    return 'Permisos insuficientes para realizar esta operación.'
  }

  if (isTrueAuthorityLoss(status ?? 0, message, code)) {
    return 'El control físico fue tomado por otro operador o expiró.'
  }

  if (lowerCode === 'inference_discarded' || lower.includes('inference result discarded')) {
    return 'La lectura fue descartada porque cambió la escena o la configuración. Repite el análisis con la fuente activa.'
  }

  if (lowerCode === 'stream_stale' || lower.includes('stream run was stopped') || lower.includes('newer stream run is active')) {
    return 'La ejecución continua anterior ya terminó. Activa nuevamente el modo continuo.'
  }

  if (
    lowerCode === 'source_stale' ||
    lower.includes('stream source is not active') ||
    lower.includes('photo source is not active') ||
    lower.includes('stream source version is stale') ||
    lower.includes('photo source version is stale') ||
    lower.includes('source changed during intake') ||
    lower.includes('scene changed')
  ) {
    return 'La fuente de cámara cambió o está desactualizada. Selecciona la cámara activa nuevamente.'
  }

  if (
    lowerCode === 'calibration_incompatible' ||
    lowerCode === 'calibration_missing' ||
    lower.includes('calibration profile is incompatible') ||
    lower.includes('no calibration background available') ||
    lower.includes('background calibration is in progress') ||
    lower.includes('calibration superseded')
  ) {
    return 'La calibración de la bandeja no coincide con la resolución o está en progreso. Recalibra la fuente.'
  }

  if (
    lowerCode === 'preparation_mode' ||
    lower.includes('preparation mode') ||
    lower.includes('gallery missing') ||
    lower.includes('gallery unavailable')
  ) {
    return 'El sistema está en modo preparación. Ve a Calibración para guardar fondo/ROI y preparar la galería (las fotos de referencia son opcionales).'
  }

  if (
    lowerCode === 'maintenance_active' ||
    lower.includes('hardware is in maintenance mode') ||
    lower.includes('must enter maintenance mode')
  ) {
    return 'El hardware se encuentra en modo mantenimiento. Sal de mantenimiento para reanudar el análisis.'
  }

  if (
    lowerCode === 'stale_frame' ||
    lower.includes('stale before queueing') ||
    lower.includes('webcam is stale') ||
    lower.includes('superseded by a newer frame') ||
    lower.includes('stale frame')
  ) {
    return 'El fotograma capturado fue descartado por retraso. Se procesará el siguiente fotograma.'
  }

  if (
    lowerCode === 'config_busy' ||
    lower.includes('preparation or apply task is currently running') ||
    lower.includes('retry after completion')
  ) {
    return 'Hay una tarea de configuración en ejecución. Espera un momento y reintenta.'
  }

  if (status === 429 || lower.includes('rate limit exceeded')) {
    return 'Límite de velocidad de fotogramas alcanzado. Ajustando tasa de captura.'
  }

  return message
}

function appWebsocketUrl(baseUrl: string, path: string): string {
  const parsed = new URL(baseUrl.replace(/\/+$/, ''))
  parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:'
  parsed.pathname = path
  parsed.search = ''
  parsed.hash = ''
  return parsed.toString()
}

function useStoredValue(key: string, fallback: string) {
  const [value, setValue] = useState(() => localStorage.getItem(key) || fallback)
  useEffect(() => { localStorage.setItem(key, value) }, [key, value])
  return [value, setValue] as const
}

function normalizeCapture(file: File): Promise<{ blob: Blob; url: string; width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const fallbackUrl = URL.createObjectURL(file)
    const finishFallback = () => {
      const image = new Image()
      image.onload = () => resolve({ blob: file, url: fallbackUrl, width: image.naturalWidth, height: image.naturalHeight })
      image.onerror = () => reject(new Error('No se pudo leer la imagen'))
      image.src = fallbackUrl
    }
    if (!('createImageBitmap' in window)) return finishFallback()
    createImageBitmap(file, { imageOrientation: 'from-image' }).then((bitmap) => {
      const canvas = document.createElement('canvas')
      canvas.width = bitmap.width; canvas.height = bitmap.height
      const context = canvas.getContext('2d')
      if (!context) { bitmap.close(); return finishFallback() }
      context.drawImage(bitmap, 0, 0)
      bitmap.close()
      canvas.toBlob((blob) => {
        if (!blob) return reject(new Error('No se pudo preparar la imagen'))
        URL.revokeObjectURL(fallbackUrl)
        resolve({ blob, url: URL.createObjectURL(blob), width: canvas.width, height: canvas.height })
      }, 'image/jpeg', 0.92)
    }).catch(finishFallback)
  })
}

function formatSimilarity(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : '—'
}

function formatMetric(value: unknown, unit: string, empty = 'Sin muestras suficientes') {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} ${unit}` : empty
}

function metricSampleLabel(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} muestras` : 'muestras no disponibles'
}

function displayLocation(location?: string) {
  const normalized = location?.trim().toLowerCase()
  return !normalized || ['unconfirmed', 'unknown', 'unassigned', 'example', 'sin confirmar'].includes(normalized)
    ? 'Ubicación por confirmar'
    : location
}

function connectionLabel(state: ConnectionState) {
  return ({ disconnected: 'Desconectado', checking: 'Verificando', connected: 'Conectado', reconnecting: 'Reconectando', error: 'Error de conexión' })[state]
}

function eventResult(event: WsEvent): AnalysisResponse | undefined {
  return event.result && typeof event.result === 'object' ? event.result as AnalysisResponse : undefined
}

function loadImageSize(url: string): Promise<{ width: number; height: number } | null> {
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight })
    image.onerror = () => resolve(null)
    image.src = url
  })
}

export default function App() {
  if (typeof window !== 'undefined' && (window.location.pathname === '/camera' || window.location.pathname === '/camera/')) {
    return <RemoteCameraView />
  }

  const [apiUrl, setApiUrl] = useStoredValue(URL_KEY, defaultApiUrl())
  const [deviceId, setDeviceId] = useStoredValue(DEVICE_KEY, `phone-${Math.random().toString(36).slice(2, 8)}`)
  const [pairingKey, setPairingKey] = useState('')
  const [requestedRole, setRequestedRole] = useState<'controller' | 'reader' | 'admin'>('controller')
  const [activeTab, setActiveTab] = useState<ActiveTab>('inspection')
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [draftConfig, setDraftConfig] = useState<ConfigResponse | null>(null)
  const [configStatus, setConfigStatus] = useState<ConfigStatusResponse | null>(null)
  const [applyingConfig, setApplyingConfig] = useState(false)
  const [session, setSession] = useState<Credentials | null>(null)
  const [connection, setConnection] = useState<ConnectionState>('disconnected')
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null)
  const [preview, setPreview] = useState<{ blob: Blob; url: string; width: number; height: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(true)
  const [fit, setFit] = useState<'contain' | 'cover'>('contain')
  const [cameraOn, setCameraOn] = useState(false)
  const [continuous, setContinuous] = useState(false)
  const [leaseMessage, setLeaseMessage] = useState('')
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [inventoryExample, setInventoryExample] = useState(false)
  const [calibrating, setCalibrating] = useState(false)
  const [viewerSize, setViewerSize] = useState({ width: 0, height: 0 })
  const [cameraSources, setCameraSources] = useState<CameraSourceDescriptor[]>([])
  const [webcams, setWebcams] = useState<WebCamDevice[]>([])
  const [activeSourceId, setActiveSourceId] = useState<string>('remote_phone')
  const [sourceVersion, setSourceVersion] = useState<number | null>(null)
  const [observedVideo, setObservedVideo] = useState<{ width: number; height: number; fps: number | null } | null>(null)
  const [qrModalOpen, setQrModalOpen] = useState<boolean>(false)
  const [activeInvite, setActiveInvite] = useState<CameraInviteResponse | null>(null)
  const [activityLogs, setActivityLogs] = useState<ActivityLogItem[]>([
    {
      id: 'init',
      time: new Date().toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      message: 'Sistema listo. Esperando conexión o captura.',
      type: 'info',
    },
  ])
  const [backendPreviewUrl, setBackendPreviewUrl] = useState<string | null>(null)
  const [backendPreviewBlob, setBackendPreviewBlob] = useState<Blob | null>(null)
  const backendPreviewBlobRef = useRef<Blob | null>(null)
  const [transmitterConnected, setTransmitterConnected] = useState<boolean>(false)
  const peerConnectionRef = useRef<RTCPeerConnection | null>(null)
  const signalingWsRef = useRef<WebSocket | null>(null)
  const previewWsRef = useRef<WebSocket | null>(null)
  const remoteStreamRef = useRef<MediaStream | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const cameraStream = useRef<MediaStream | null>(null)
  const cameraRequestGeneration = useRef(0)
  const sessionRef = useRef<Credentials | null>(null)
  const sessionGeneration = useRef(0)
  const sceneGeneration = useRef(0)
  const socketRef = useRef<{ close: () => void } | null>(null)
  const reconnectTimer = useRef<number | null>(null)
  const reconnectAttempts = useRef(0)
  const mounted = useRef(true)
  const continuousBusy = useRef(false)
  const latestFrame = useRef<Blob | null>(null)
  const streamGeneration = useRef(0)
  const activeStreamRun = useRef<number | null>(null)
  const videoFreshnessRef = useRef<VideoFreshnessState>(createVideoFreshnessState())
  const streamStopPromiseRef = useRef<Promise<unknown> | null>(null)
  const streamStopErrorRef = useRef<unknown | null>(null)
  const continuousMode = useRef(false)
  const previewUrl = useRef<string | null>(null)
  const viewerRef = useRef<HTMLDivElement>(null)
  const invalidateSessionRef = useRef<(message: string) => void>(() => undefined)
  const configRef = useRef<ConfigResponse | null>(null)
  const draftConfigRef = useRef<ConfigResponse | null>(null)

  const logActivity = useCallback((message: string, type: ActivityLogItem['type'] = 'info', dedupeKey?: string) => {
    setActivityLogs((prev) => {
      if (dedupeKey && prev.some((item) => item.dedupeKey === dedupeKey)) return prev
      if (!dedupeKey && prev.length > 0 && prev[0].message === message) return prev
      const item: ActivityLogItem = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        time: new Date().toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        message,
        type,
        dedupeKey,
      }
      return [item, ...prev].slice(0, 20)
    })
  }, [])

  configRef.current = config
  draftConfigRef.current = draftConfig

  const canControl = session?.role === 'controller' || session?.role === 'admin'

  const cleanupRemotePeer = useCallback(() => {
    remoteStreamRef.current?.getTracks().forEach((track) => track.stop())
    remoteStreamRef.current = null
    peerConnectionRef.current?.close()
    peerConnectionRef.current = null
  }, [])

  const cleanupRemoteConnection = useCallback(() => {
    cleanupRemotePeer()
    signalingWsRef.current?.close()
    signalingWsRef.current = null
  }, [cleanupRemotePeer])

  const observeVideoStream = useCallback((stream: MediaStream) => {
    const video = videoRef.current
    if (!video) return
    const update = () => {
      if (cameraStream.current !== stream && remoteStreamRef.current !== stream) return
      const track = stream.getVideoTracks?.()[0]
      const settings = track?.getSettings?.() as MediaTrackSettings | undefined
      const width = video.videoWidth || Number(settings?.width) || 0
      const height = video.videoHeight || Number(settings?.height) || 0
      if (width > 0 && height > 0) {
        const fps = Number(settings?.frameRate)
        setObservedVideo({ width, height, fps: Number.isFinite(fps) && fps > 0 ? fps : null })
      }
    }
    video.addEventListener('loadedmetadata', update, { once: true })
    let attempts = 0
    const poll = () => {
      update()
      attempts += 1
      if (attempts < 20) window.setTimeout(poll, 100)
    }
    poll()
    void video.play().then(update).catch(() => undefined)
  }, [])

  const hasLocalDraftChanges = useMemo(() => {
    if (!config || !draftConfig) return false
    return (
      JSON.stringify(config.inventory) !== JSON.stringify(draftConfig.inventory) ||
      JSON.stringify(config.settings) !== JSON.stringify(draftConfig.settings)
    )
  }, [config, draftConfig])

  const draftsMatchServer = useCallback(() => {
    const server = configRef.current
    const draft = draftConfigRef.current
    if (!server || !draft) return true
    return JSON.stringify(server.settings) === JSON.stringify(draft.settings) &&
      JSON.stringify(server.inventory) === JSON.stringify(draft.inventory)
  }, [])

  const refreshServerConfigIfClean = useCallback(() => {
    const requestSession = sessionRef.current
    const requestGeneration = sessionGeneration.current
    if (!requestSession || !draftsMatchServer()) return
    void getConfig(apiUrl, requestSession).then((latest) => {
      if (
        mounted.current &&
        sessionRef.current === requestSession &&
        sessionGeneration.current === requestGeneration &&
        draftsMatchServer()
      ) {
        setConfig(latest)
        setDraftConfig(latest)
      }
    }).catch(() => undefined)
  }, [apiUrl, draftsMatchServer])

  const mergeReferenceRefresh = useCallback((latest: ConfigResponse, acceptedSettings: Array<keyof SettingsConfig> = []) => {
    setConfig(latest)
    setDraftConfig((previous) => {
      if (!previous) return latest
      const server = configRef.current
      const dirty = server && (
        JSON.stringify(server.settings) !== JSON.stringify(previous.settings) ||
        JSON.stringify(server.inventory) !== JSON.stringify(previous.inventory)
      )
      if (!dirty) return latest
      const remoteById = new Map((latest.inventory.categories || []).map((item) => [item.class_id, item]))
      return {
        ...previous,
        saved_version: latest.saved_version,
        active_version: latest.active_version,
        is_dirty: true,
        settings: {
          ...previous.settings,
          ...Object.fromEntries(acceptedSettings.map((key) => [key, latest.settings[key]])),
        } as SettingsConfig,
        inventory: {
          ...previous.inventory,
          categories: previous.inventory.categories.map((item) => ({
            ...item,
            reference_count: remoteById.get(item.class_id)?.reference_count ?? item.reference_count,
          })),
        },
      }
    })
  }, [])

  const setPreviewSafely = useCallback((next: { blob: Blob; url: string; width: number; height: number }) => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current)
    previewUrl.current = next.url
    setPreview(next)
  }, [])

  const acceptResult = useCallback(async (result: AnalysisResponse, fallback?: Blob, isCurrent: () => boolean = () => true) => {
    if (!isCurrent()) return
    const remote = result.image.preview_url || result.image.preview_data_url
    if (remote) {
      const natural = await loadImageSize(remote)
      if (!isCurrent()) return
      if (!natural || natural.width !== result.image.width || natural.height !== result.image.height) {
        if (fallback && preview && preview.width === result.image.width && preview.height === result.image.height) {
          setPreviewSafely({ blob: fallback, url: URL.createObjectURL(fallback), width: result.image.width, height: result.image.height })
        } else {
          setError('El preview normalizado del backend no coincide con sus dimensiones declaradas; se ocultaron las cajas.')
          setAnalysis(null)
          return
        }
      } else setPreviewSafely({ blob: fallback || preview?.blob || new Blob(), url: remote, width: natural.width, height: natural.height })
    }
    if (!remote && fallback && preview?.blob === fallback && (preview.width !== result.image.width || preview.height !== result.image.height)) {
      setError('La imagen local y las dimensiones normalizadas del backend no coinciden; se conserva la captura sin overlay.')
      setAnalysis(null)
      return
    }
    if (remote) {
      setPreviewSafely({ blob: fallback || preview?.blob || new Blob(), url: remote, width: result.image.width, height: result.image.height })
    } else if (fallback) {
      setPreviewSafely({ blob: fallback, url: URL.createObjectURL(fallback), width: result.image.width, height: result.image.height })
    } else if (preview && (preview.width !== result.image.width || preview.height !== result.image.height)) {
      setError('La imagen local y las dimensiones normalizadas del backend no coinciden; se conservó la última vista válida.')
      return
    }
    setAnalysis(result)
    const detectedNames = (result.detections || []).map((d: any) => d.name || d.class_id).filter(Boolean)
    if (detectedNames.length > 0) {
      logActivity(`Detección: ${detectedNames.join(', ')}`, 'success', `detection:${result.frame_id}`)
    } else {
      logActivity('Inspección: Sin herramientas detectadas', 'info', `detection:${result.frame_id}`)
    }
    if (result.arduino?.ack) {
      logActivity(`ACK Hardware: MASK 0x${(result.arduino.confirmed_mask || 0).toString(16).toUpperCase()}`, 'info', `ack:${result.frame_id}`)
    }
  }, [logActivity, preview, setPreviewSafely])

  const handleEvent = useCallback((event: WsEvent, isCurrent: () => boolean = () => true) => {
    const result = eventResult(event)
    if (continuousMode.current && event.type === 'detection') return
    if (result && sessionRef.current && result.frame_id) void acceptResult(result, undefined, isCurrent)
    if (event.type === 'system' && event.event === 'controller_released') setLeaseMessage('El control fue liberado por el servidor.')
    if (event.type === 'system') {
      if (event.event === 'config_changed') {
        const sVer = Number(event.saved_version ?? 1)
        const aVer = Number(event.active_version ?? 1)
        setConfig((prev) => prev ? {
          ...prev,
          saved_version: sVer,
          active_version: aVer,
          is_dirty: Boolean(event.is_dirty),
        } : prev)
        refreshServerConfigIfClean()
      } else if (event.event === 'config_applied' || event.event === 'gallery_ready') {
        const nextActive = Number(event.active_version ?? 1)
        setConfig((prev) => prev ? {
          ...prev,
          active_version: nextActive,
          is_dirty: false,
        } : prev)
        if (draftsMatchServer()) {
          setDraftConfig((prev) => prev ? { ...prev, active_version: nextActive, is_dirty: false } : prev)
          refreshServerConfigIfClean()
        }
      } else if (event.event === 'config_rolled_back') {
        const sVer = Number(event.saved_version ?? 1)
        const aVer = Number(event.active_version ?? 1)
        setConfig((prev) => prev ? {
          ...prev,
          saved_version: sVer,
          active_version: aVer,
          is_dirty: false,
        } : prev)
        refreshServerConfigIfClean()
      } else if (event.event === 'mode_changed' && event.mode) {
        const nextMode = String(event.mode)
        setStatus((prev) => prev ? { ...prev, mode: nextMode } : prev)
        setConfig((prev) => prev ? {
          ...prev,
          settings: { ...prev.settings, mode: nextMode as 'mock' | 'preparation' | 'real' },
        } : prev)
        if (draftsMatchServer()) {
          setDraftConfig((prev) => prev ? {
            ...prev,
            settings: { ...prev.settings, mode: nextMode as 'mock' | 'preparation' | 'real' },
          } : prev)
        }
      } else if (event.event === 'roi_updated' && Array.isArray(event.roi)) {
        const nextRoi = event.roi as [number, number, number, number]
        setConfig((prev) => prev ? {
          ...prev,
          settings: { ...prev.settings, roi: nextRoi },
        } : prev)
        if (draftsMatchServer()) {
          setDraftConfig((prev) => prev ? {
            ...prev,
            settings: { ...prev.settings, roi: nextRoi },
          } : prev)
        }
      } else if (event.event === 'task_status' && event.task) {
        setConfigStatus((prev) => ({
          saved_version: prev?.saved_version ?? 1,
          active_version: prev?.active_version ?? 1,
          is_dirty: prev?.is_dirty ?? false,
          task: event.task as ConfigStatusResponse['task'],
        }))
      } else if (event.event === 'gallery_building') {
        setConfigStatus((prev) => ({
          saved_version: prev?.saved_version ?? 1,
          active_version: prev?.active_version ?? 1,
          is_dirty: prev?.is_dirty ?? false,
          task: {
            state: 'running',
            stage: String(event.stage || 'building'),
            progress: Number(event.progress || 0),
            error: null,
          },
        }))
      } else if (event.event === 'gallery_error') {
        setConfigStatus((prev) => ({
          saved_version: prev?.saved_version ?? 1,
          active_version: prev?.active_version ?? 1,
          is_dirty: prev?.is_dirty ?? false,
          task: {
            state: 'failed',
            stage: 'failed',
            progress: 0,
            error: String(event.detail || 'Error en galería'),
          },
        }))
      } else if (event.event === 'arduino_state_changed' && event.arduino) {
        setStatus((prev) => prev ? { ...prev, arduino: event.arduino as StatusResponse['arduino'] } : prev)
      } else if (event.event === 'maintenance_mode') {
        const nextMaint = Boolean(event.maintenance_mode)
        setStatus((prev) => prev ? {
          ...prev,
          maintenance_mode: nextMaint,
          arduino: (event.arduino as StatusResponse['arduino']) || prev?.arduino,
        } : prev)
        if (nextMaint) {
          cameraRequestGeneration.current += 1
          streamGeneration.current += 1
          cameraStream.current?.getTracks().forEach((track) => track.stop())
          cameraStream.current = null
          if (videoRef.current) videoRef.current.srcObject = null
          setCameraOn(false)
          setContinuous(false)
        }
      }
    }
    const ack = event.arduino || (event.requested_mask !== undefined ? event : undefined)
    if (event.type === 'arduino_ack' && ack && isCurrent()) {
      setAnalysis((current) => current ? { ...current, arduino: ack as AnalysisResponse['arduino'] } : current)
      setStatus((prev) => prev ? { ...prev, arduino: ack as StatusResponse['arduino'] } : prev)
    }
  }, [acceptResult, draftsMatchServer, refreshServerConfigIfClean])

  const connectSocket = useCallback((credentials: Credentials) => {
    socketRef.current?.close()
    const socketScene = sceneGeneration.current
    void createWsTicket(apiUrl, credentials).then((ws) => {
      if (!mounted.current || !sessionRef.current || sessionRef.current.sessionId !== credentials.sessionId || socketScene !== sceneGeneration.current) return
      const socketCredentials = { ...credentials, wsTicket: ws.ticket }
      socketRef.current = openEvents(apiUrl, socketCredentials, (event) => {
        if (socketScene === sceneGeneration.current) void handleEvent(event, () => socketScene === sceneGeneration.current && mounted.current)
      }, (state) => {
        if (!mounted.current || !sessionRef.current) return
        if (state === 'open') { reconnectAttempts.current = 0; setConnection('connected') }
        if (state === 'error' || state === 'closed') {
          reconnectAttempts.current += 1
          if (reconnectAttempts.current > MAX_RECONNECT_ATTEMPTS) {
            invalidateSessionRef.current('La sesión WebSocket expiró. Empareja el teléfono de nuevo.')
            return
          }
          setConnection('reconnecting')
          if (reconnectTimer.current === null) reconnectTimer.current = window.setTimeout(() => {
            reconnectTimer.current = null
            if (!mounted.current || !sessionRef.current) return
            void getStatus(apiUrl, credentials, 4000).then(() => connectSocket(credentials)).catch((caught) => {
              if (caught instanceof ApiError && caught.status === 401) {
                invalidateSessionRef.current('La sesión expiró. Empareja de nuevo.')
              } else if (caught instanceof ApiError && caught.status === 409) {
                if (isTrueAuthorityLoss(caught.status, caught.message, caught.code)) {
                  setLeaseMessage('El control físico fue tomado por otro operador o expiró.')
                  setContinuous(false)
                }
              } else if (mounted.current) connectSocket(credentials)
            })
          }, 1500)
        }
      })
    }).catch((caught) => {
      if (caught instanceof ApiError && caught.status === 401) invalidateSessionRef.current('La sesión expiró. Empareja de nuevo.')
      else if (caught instanceof ApiError && caught.status === 409) {
        if (isTrueAuthorityLoss(caught.status, caught.message, caught.code)) {
          setLeaseMessage('El control físico fue tomado por otro operador o expiró.')
        }
      } else if (mounted.current) setError(mapActionableErrorMessage(caught))
    })
  }, [apiUrl, handleEvent])

  const connect = async () => {
    setError(''); setLeaseMessage(''); setConnection('checking')
    let created: Credentials | null = null
    try {
      const response = await createSession(apiUrl, pairingKey, deviceId, requestedRole)
      created = { sessionId: response.session_id, ticket: response.ticket, role: (response.role || requestedRole) as 'controller' | 'reader' | 'admin' }
      const credentials: Credentials = created
      sessionGeneration.current += 1
      sceneGeneration.current += 1
      reconnectAttempts.current = 0
      sessionRef.current = credentials
      setSession(credentials)
      const [nextStatus, nextInventory] = await Promise.all([getStatus(apiUrl, credentials), getInventory(apiUrl)])
      if (sessionRef.current !== credentials) return
      setStatus(nextStatus)
      setInventory(nextInventory.categories || [])
      setInventoryExample(Boolean(nextInventory.example))
      try {
        const nextConfig = await getConfig(apiUrl, credentials)
        if (sessionRef.current === credentials) {
          setConfig(nextConfig)
          setDraftConfig(nextConfig)
        }
      } catch (caught) {
        if (sessionRef.current === credentials) {
          setError(caught instanceof Error ? caught.message : 'No se pudo cargar la configuración')
        }
      }
      try {
        const nextConfigStatus = await getConfigStatus(apiUrl, credentials)
        if (sessionRef.current === credentials) setConfigStatus(nextConfigStatus)
      } catch {
        /* status is non-fatal if endpoint is temporarily unavailable */
      }
      connectSocket(credentials)
      setConnection('connected')
    } catch (caught) {
      if (created) void releaseSession(apiUrl, created).catch(() => undefined)
      sessionRef.current = null
      setSession(null)
      setConnection('error')
      setError(caught instanceof ApiError && caught.status === 409 ? 'Otro operador/dispositivo ya controla el Arduino. Libera esa sesión antes de conectar.' : caught instanceof Error ? caught.message : 'No se pudo conectar')
    }
  }

  const refreshSources = useCallback(async () => {
    if (!session || typeof getCameraSources !== 'function') return
    try {
      const res = await getCameraSources(apiUrl, session)
      if (mounted.current && res?.sources) {
        setCameraSources(res.sources)
        if (res.active_source_id) setActiveSourceId(res.active_source_id)
        if (Number.isFinite(res.source_version)) setSourceVersion(res.source_version)
      }
      if (typeof getWebcams === 'function') {
        const webcamResponse = await getWebcams(apiUrl, session)
        if (mounted.current) setWebcams(webcamResponse.devices || [])
      }
    } catch {
      // Ignored in mock/offline
    }
  }, [apiUrl, session])

  const handleSelectWebcam = async (sourceId: string, device: WebCamDevice) => {
    if (!session || typeof updateCameraSourceSettings !== 'function') return
    try {
      await updateCameraSourceSettings(apiUrl, session, sourceId, {
        device_index: device.index,
        width: device.width,
        height: device.height,
        fps: device.fps,
      })
      logActivity(`Perfil de captura actualizado: ${device.name}`, 'info')
      await refreshSources()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar la cámara web')
    }
  }

  const handleSelectSource = async (sourceId: string) => {
    if (!session || typeof setActiveCameraSource !== 'function') return
    try {
      cameraRequestGeneration.current += 1
      streamGeneration.current += 1
      cameraStream.current?.getTracks().forEach((track) => track.stop())
      cameraStream.current = null
      cleanupRemoteConnection()
      if (videoRef.current) videoRef.current.srcObject = null
      setCameraOn(false)
      setContinuous(false)
      setObservedVideo(null)
      setAnalysis(null)
      setPreview(null)
      sceneGeneration.current += 1
      await setActiveCameraSource(apiUrl, session, sourceId)
      setActiveSourceId(sourceId)
      logActivity(`Cambio de fuente a: ${sourceId}`, 'info')
      await refreshSources()
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Error al cambiar fuente de cámara')
    }
  }

  const handleOpenInvite = async () => {
    if (!session || typeof createCameraInvite !== 'function') {
      setError('Conecta una sesión de controlador para generar invitaciones.')
      return
    }
    try {
      const invite = await createCameraInvite(apiUrl, session)
      setActiveInvite(invite)
      setQrModalOpen(true)
      logActivity('Invitación QR generada para cámara celular', 'info')
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Error al generar código QR')
    }
  }

  const handleCancelInvite = async () => {
    if (!session || !activeInvite || typeof cancelCameraInvite !== 'function') return
    try {
      await cancelCameraInvite(apiUrl, session, activeInvite.invite_id)
      setActiveInvite(null)
      logActivity('Invitación QR cancelada', 'warning')
    } catch {
      // Ignore
    }
  }

  const handleRevokeTransmitters = async () => {
    if (!session || typeof revokeCameraTransmitters !== 'function') return
    try {
      const res = await revokeCameraTransmitters(apiUrl, session)
      cleanupRemotePeer()
      setCameraOn(false)
      setObservedVideo(null)
      logActivity(`Transmisores revocados: ${res.disconnected_transmitters}`, 'warning')
      setLeaseMessage('Transmisores desconectados y revocados.')
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Error al revocar transmisores')
    }
  }

  const handleTakeover = async () => {
    if (!session || session.role !== 'admin' || typeof takeoverLease !== 'function') return
    try {
      const res = await takeoverLease(apiUrl, session)
      if (res.success) {
        logActivity('Control físico expropiado por administrador', 'warning')
        setLeaseMessage('Has tomado control físico exclusivo del sistema.')
      }
    } catch (err: any) {
      setError(err instanceof Error ? err.message : 'Error al tomar control')
    }
  }

  useEffect(() => {
    if (session) void refreshSources()
  }, [refreshSources, session])

  useEffect(() => {
    if (!session || (session.role !== 'controller' && session.role !== 'admin') || typeof sessionHeartbeat !== 'function') return
    const current = session
    const timer = setInterval(() => {
      void sessionHeartbeat(apiUrl, current).then((res) => {
        if (res && res.lease_held === false) {
          setLeaseMessage('El control físico fue tomado por otro operador o expiró.')
          setContinuous(false)
        }
      }).catch((caught) => {
        if (caught instanceof ApiError && caught.status === 401) {
          invalidateSessionRef.current('La sesión expiró. Empareja de nuevo.')
        }
      })
    }, 4500)
    return () => clearInterval(timer)
  }, [apiUrl, session])

  // WebRTC Operator Signaling for remote_phone
  useEffect(() => {
    if (!session || !canControl || activeSourceId !== 'remote_phone') {
      cleanupRemoteConnection()
      if (videoRef.current && !cameraStream.current) videoRef.current.srcObject = null
      setTransmitterConnected(false)
      if (!cameraStream.current) {
        setObservedVideo(null)
        setCameraOn(false)
      }
      return
    }

    let active = true
    const wsUrl = appWebsocketUrl(apiUrl, '/v1/camera/signal')
    let ws: WebSocket | null = null
    let pendingCandidates: RTCIceCandidateInit[] = []

    try {
      ws = new WebSocket(wsUrl)
      signalingWsRef.current = ws

      ws.onopen = () => {
        if (!active || !ws) return
        ws.send(
          JSON.stringify({
            type: 'auth_operator',
            session_id: session.sessionId,
            ticket: session.ticket,
          })
        )
      }

      ws.onmessage = async (evt) => {
        if (!active) return
        try {
          const msg = JSON.parse(evt.data)
          if (msg.type === 'auth_ok') {
            logActivity('Operador autenticado en señalización P2P', 'info')
          } else if (msg.type === 'peer_connected') {
            setTransmitterConnected(true)
            logActivity('Cámara remota conectada. Estableciendo WebRTC P2P...', 'info')

            peerConnectionRef.current?.close()
            const pc = new RTCPeerConnection({ iceServers: [] })
            peerConnectionRef.current = pc
            pendingCandidates = []
            pc.addTransceiver('video', { direction: 'recvonly' })

            pc.ontrack = (event) => {
              if (!active) return
              if (event.streams[0]) {
                remoteStreamRef.current = event.streams[0]
                videoFreshnessRef.current = createVideoFreshnessState()
                if (videoRef.current) {
                  videoRef.current.srcObject = event.streams[0]
                  void videoRef.current.play().catch(() => {})
                }
                observeVideoStream(event.streams[0])
                setCameraOn(true)
                logActivity('Video WebRTC P2P en vivo recibido', 'info')
              }
            }

            pc.onicecandidate = (event) => {
              if (event.candidate && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'candidate', candidate: event.candidate }))
              }
            }

            const offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            if (ws && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'offer', sdp: offer }))
            }
          } else if (msg.type === 'answer') {
            if (peerConnectionRef.current) {
              await peerConnectionRef.current.setRemoteDescription(new RTCSessionDescription(msg.sdp))
              for (const candidate of pendingCandidates.splice(0)) {
                await peerConnectionRef.current.addIceCandidate(new RTCIceCandidate(candidate)).catch(() => {})
              }
            }
          } else if (msg.type === 'candidate') {
            if (peerConnectionRef.current && msg.candidate) {
              if (peerConnectionRef.current.remoteDescription) {
                await peerConnectionRef.current.addIceCandidate(new RTCIceCandidate(msg.candidate)).catch(() => {})
              } else {
                pendingCandidates.push(msg.candidate as RTCIceCandidateInit)
              }
            }
          } else if (msg.type === 'peer_disconnected') {
            setTransmitterConnected(false)
            cleanupRemotePeer()
            if (videoRef.current) videoRef.current.srcObject = null
            setCameraOn(false)
            setObservedVideo(null)
            logActivity('Cámara remota desconectada', 'warning')
          }
        } catch {
          // Ignore parse errors
        }
      }

      ws.onclose = () => {
        if (active) setTransmitterConnected(false)
      }
    } catch {
      // WS constructor error
    }

    return () => {
      active = false
      if (ws) ws.close()
      cleanupRemotePeer()
      if (signalingWsRef.current === ws) signalingWsRef.current = null
      pendingCandidates = []
      if (videoRef.current && !cameraStream.current) videoRef.current.srcObject = null
      setTransmitterConnected(false)
      setCameraOn(false)
    }
  }, [activeSourceId, apiUrl, canControl, cleanupRemoteConnection, cleanupRemotePeer, logActivity, observeVideoStream, session])

  // Backend Binary Preview WebSocket for OpenCV and remote fallback sources.
  useEffect(() => {
    const wantsBackendPreview = activeSourceId === 'remote_phone' || activeSourceId === 'laptop_builtin' || activeSourceId === 'usb_webcam'
    if (!session || !wantsBackendPreview) {
      if (previewWsRef.current) {
        previewWsRef.current.close()
        previewWsRef.current = null
      }
      if (backendPreviewUrl) {
        URL.revokeObjectURL(backendPreviewUrl)
        setBackendPreviewUrl(null)
      }
      setBackendPreviewBlob(null)
      backendPreviewBlobRef.current = null
      return
    }

    let active = true
    const wsUrl = appWebsocketUrl(apiUrl, '/v1/camera/preview-ws')
    let ws: WebSocket | null = null

    try {
      ws = new WebSocket(wsUrl)
      ws.binaryType = 'arraybuffer'
      previewWsRef.current = ws

      ws.onopen = () => {
        if (active && ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'auth', session_id: session.sessionId, ticket: session.ticket }))
        }
      }

      ws.onmessage = (evt) => {
        if (!active) return
        if (evt.data instanceof ArrayBuffer || evt.data instanceof Blob) {
          const blob = new Blob([evt.data], { type: 'image/jpeg' })
          const url = URL.createObjectURL(blob)
          setBackendPreviewUrl((prev) => {
            if (prev) URL.revokeObjectURL(prev)
            return url
          })
           setBackendPreviewBlob(blob)
           backendPreviewBlobRef.current = blob
        }
      }
    } catch {
      // Ignore
    }

    return () => {
      active = false
      if (ws) ws.close()
      setBackendPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev)
        return null
      })
       setBackendPreviewBlob(null)
       backendPreviewBlobRef.current = null
    }
  }, [activeSourceId, apiUrl, session])

  const handleApplyConfig = async () => {
    if (!session || session.role !== 'admin') return
    setApplyingConfig(true)
    setError('')
    try {
      if (hasLocalDraftChanges && draftConfig && config) {
        const saved = await updateConfig(apiUrl, session, {
          expected_version: draftConfig.saved_version,
          settings: draftConfig.settings,
          inventory: draftConfig.inventory,
        })
        setConfig(saved)
        setDraftConfig(saved)
      }
      const res = await applyConfig(apiUrl, session)
      setConfig(res)
      setDraftConfig(res)
      setInventory(res.inventory?.categories || [])
      setInventoryExample(Boolean(res.inventory?.example))
    } catch (caught: unknown) {
      const isConflict =
        (caught instanceof ApiError && caught.status === 409) ||
        (typeof caught === 'object' && caught !== null && 'status' in caught && (caught as { status: number }).status === 409)
      if (isConflict) {
        setError('Conflicto de versiones al guardar: otro cambio modificó la configuración en el servidor. Revisa los cambios o recarga la configuración.')
      } else {
        setError(caught instanceof Error ? caught.message : 'Error al aplicar configuración')
      }
    } finally {
      setApplyingConfig(false)
    }
  }

  const handleConfigUpdated = useCallback((next: ConfigResponse) => {
    setConfig(next)
    setDraftConfig(next)
  }, [])

  const stopCamera = useCallback((preserveRemote = false) => {
    cameraRequestGeneration.current += 1
    streamGeneration.current += 1
    latestFrame.current = null
    videoFreshnessRef.current = createVideoFreshnessState()
    continuousBusy.current = false
    cameraStream.current?.getTracks().forEach((track) => track.stop())
    cameraStream.current = null
    if (!preserveRemote) cleanupRemoteConnection()
    if (videoRef.current) videoRef.current.srcObject = null
    setObservedVideo(null)
    setCameraOn(false); setContinuous(false)
  }, [cleanupRemoteConnection])

  const requestStreamStop = useCallback((credentials: Credentials, sourceContext?: { sourceId?: string; sourceVersion?: number; streamRun?: number }) => {
    if (streamStopPromiseRef.current) return streamStopPromiseRef.current
    streamStopErrorRef.current = null
    const request = stopStream(apiUrl, credentials, 5000, sourceContext).then(
      (result) => {
        streamStopErrorRef.current = null
        return result
      },
      (caught) => {
        streamStopErrorRef.current = caught
        throw caught
      },
    )
    streamStopPromiseRef.current = request
    void request.finally(() => {
      if (streamStopPromiseRef.current === request) streamStopPromiseRef.current = null
    }).catch(() => undefined)
    return request
  }, [apiUrl])

  const stopContinuousForAction = useCallback(async () => {
    const activeRun = activeStreamRun.current
    const wasContinuous = continuousMode.current || activeRun !== null
    continuousMode.current = false
    streamGeneration.current += 1
    setContinuous(false)
    const credentials = sessionRef.current
    if (credentials && wasContinuous) await requestStreamStop(credentials, { sourceId: activeSourceId, sourceVersion: sourceVersion ?? undefined, streamRun: activeRun ?? undefined })
  }, [activeSourceId, requestStreamStop, sourceVersion])

  const invalidateSession = useCallback((message: string) => {
    sessionGeneration.current += 1
    sceneGeneration.current += 1
    streamGeneration.current += 1
    sessionRef.current = null
    socketRef.current?.close(); socketRef.current = null
    if (reconnectTimer.current !== null) { window.clearTimeout(reconnectTimer.current); reconnectTimer.current = null }
    reconnectAttempts.current = 0
    stopCamera()
    latestFrame.current = null; continuousBusy.current = false
    setSession(null); setStatus(null); setConfig(null); setConfigStatus(null); setInventory([]); setInventoryExample(false); setAnalysis(null); setConnection('error'); setLeaseMessage(message)
  }, [stopCamera])

  invalidateSessionRef.current = invalidateSession

  const release = useCallback(async () => {
    const current = session
    invalidateSession('Control liberado.')
    setConnection('disconnected')
    if (current) {
      try { await releaseSession(apiUrl, current) } catch { /* expiry already releases the lease */ }
    }
  }, [apiUrl, invalidateSession, session])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      sessionGeneration.current += 1
      sceneGeneration.current += 1
      socketRef.current?.close()
      stopCamera()
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current)
    }
  }, [stopCamera])

  const chooseImage = async (file?: File) => {
    if (!file) return
    try {
      await stopContinuousForAction()
      if (streamStopErrorRef.current) throw streamStopErrorRef.current
    } catch (caught) {
      setError(mapActionableErrorMessage(caught))
      return
    }
    sceneGeneration.current += 1
    latestFrame.current = null
    setError(''); setAnalysis(null)
    try {
      const next = await normalizeCapture(file)
      if (!mounted.current) { URL.revokeObjectURL(next.url); return }
      setPreviewSafely(next)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'No se pudo leer la foto') }
  }

  const captureFromVideo = async (): Promise<Blob | null> => {
    const video = videoRef.current
    if (!video || !video.videoWidth) return null
    try {
      await waitForFreshVideoFrame(video, videoFreshnessRef.current)
    } catch (caught) {
      setError(mapActionableErrorMessage(caught))
      return null
    }
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth; canvas.height = video.videoHeight
    canvas.getContext('2d')?.drawImage(video, 0, 0)
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
  }

  const inspect = async (blob = preview?.blob) => {
    if (!session) { setError('Conecta una sesión de controlador antes de analizar.'); return }
    if (status?.maintenance_mode) { setError('El análisis está deshabilitado durante el modo de mantenimiento de Arduino.'); return }
    const requestSession = session
    const requestSessionGeneration = sessionGeneration.current
    const requestSceneGeneration = sceneGeneration.current
    setBusy(true); setError('')
    try {
      if (streamStopErrorRef.current) throw streamStopErrorRef.current
      if (streamStopPromiseRef.current) await streamStopPromiseRef.current
      if (activeSourceId === 'laptop_builtin' || activeSourceId === 'usb_webcam' || activeSourceId === 'remote_phone') {
        const backendCaptureSource = activeSourceId === 'laptop_builtin' || activeSourceId === 'usb_webcam'
        const fallbackBlob = blob || backendPreviewBlob
        const response: any = await scanTools(apiUrl, requestSession, backendCaptureSource ? undefined : blob)
        const analysisData = response.result || response
        if (response.preview_url && mounted.current) {
            setPreviewSafely({
            blob: fallbackBlob || new Blob([]),
            url: response.preview_url,
            width: analysisData.image?.width || 640,
            height: analysisData.image?.height || 480,
          })
        }
        if (mounted.current && sessionRef.current === requestSession && sessionGeneration.current === requestSessionGeneration && sceneGeneration.current === requestSceneGeneration) {
          void acceptResult(analysisData, fallbackBlob || new Blob([]), () => mounted.current && sessionRef.current === requestSession && sessionGeneration.current === requestSessionGeneration && sceneGeneration.current === requestSceneGeneration)
        }
      } else {
        if (!blob) { setError('Selecciona una imagen o inicia la cámara.'); return }
        const result = await analyzePhoto(apiUrl, requestSession, blob)
        if (mounted.current && sessionRef.current === requestSession && sessionGeneration.current === requestSessionGeneration && sceneGeneration.current === requestSceneGeneration) void acceptResult(result, blob, () => mounted.current && sessionRef.current === requestSession && sessionGeneration.current === requestSessionGeneration && sceneGeneration.current === requestSceneGeneration)
      }
    }
    catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        invalidateSession('La sesión expiró o es inválida. Empareja de nuevo.')
      } else if (caught instanceof ApiError && caught.status === 409) {
        if (isTrueAuthorityLoss(caught.status, caught.message, caught.code)) {
          setLeaseMessage('El control físico fue tomado por otro operador o expiró.')
          setContinuous(false)
          stopCamera()
        }
      }
      setError(mapActionableErrorMessage(caught))
    } finally { setBusy(false) }
  }

  const startCamera = async () => {
    if (!canControl) { setError('El modo lector puede consultar resultados, pero no controla la cámara ni el Arduino.'); return }
    if (status?.maintenance_mode) { setError('La cámara está deshabilitada durante el modo de mantenimiento de Arduino.'); return }
    const selectedSource = cameraSources.find((source) => source.source_id === activeSourceId)
    if (!selectedSource) {
      setError('Aún no hay una fuente de cámara válida disponible.')
      return
    }
    if (selectedSource.kind !== 'browser') {
      if (selectedSource.source_id === 'remote_phone' && transmitterConnected) {
        const existingRemote = remoteStreamRef.current
        const liveRemote = existingRemote?.getVideoTracks?.().some((track) => track.readyState !== 'ended')
          ? existingRemote
          : (() => {
            const tracks = peerConnectionRef.current?.getReceivers?.().map((receiver) => receiver.track).filter((track) => track && track.readyState !== 'ended') || []
            return tracks.length ? new MediaStream(tracks) : null
          })()
        if (liveRemote) {
          remoteStreamRef.current = liveRemote
          videoFreshnessRef.current = createVideoFreshnessState()
          if (videoRef.current) {
            videoRef.current.srcObject = liveRemote
            void videoRef.current.play().catch(() => {})
          }
          observeVideoStream(liveRemote)
          setError('')
          setCameraOn(true)
          return
        }
      }
      setError(selectedSource.source_id === 'remote_phone'
        ? 'La cámara remota no está conectada. Usa Vincular Celular (QR) para recuperar la transmisión.'
        : 'La cámara de esta pantalla solo está disponible al seleccionar la fuente operator_device.')
      return
    }
    const requestGeneration = ++cameraRequestGeneration.current
    cameraStream.current?.getTracks().forEach((track) => track.stop())
    cameraStream.current = null
    setCameraOn(false)
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      if (!mounted.current || requestGeneration !== cameraRequestGeneration.current || !sessionRef.current || (sessionRef.current.role !== 'controller' && sessionRef.current.role !== 'admin')) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      videoFreshnessRef.current = createVideoFreshnessState()
      cameraStream.current = stream; setCameraOn(true)
      observeVideoStream(stream)
    } catch { setError('La cámara requiere HTTPS o localhost y permiso del navegador. También puedes elegir una foto.') }
  }

  useEffect(() => {
    const video = videoRef.current
    const stream = cameraStream.current || remoteStreamRef.current
    if (!cameraOn || !stream || !video) return
    video.srcObject = stream
    observeVideoStream(stream)
  }, [activeSourceId, cameraOn, observeVideoStream, transmitterConnected])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    const update = () => setViewerSize({ width: viewer.clientWidth, height: viewer.clientHeight })
    update()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(update)
    observer.observe(viewer)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!session) return
    const current = session
    const poll = window.setInterval(() => {
      void getStatus(apiUrl, current, 4000).then((next) => {
        if (mounted.current && sessionRef.current === current) {
          setStatus((prev) => prev ? {
            ...prev,
            ...next,
            maintenance_mode: next.maintenance_mode !== undefined ? Boolean(next.maintenance_mode) : prev.maintenance_mode,
          } : next)
        }
      }).catch((caught) => {
        if (caught instanceof ApiError && caught.status === 401) {
          invalidateSession('La sesión expiró. Empareja el teléfono de nuevo.')
        } else if (caught instanceof ApiError && caught.status === 409) {
          if (isTrueAuthorityLoss(caught.status, caught.message, caught.code)) {
            setLeaseMessage('El control físico fue tomado por otro operador o expiró.')
            setContinuous(false)
            stopCamera()
          }
        }
      })
    }, 2000)
    return () => window.clearInterval(poll)
  }, [apiUrl, invalidateSession, session, stopCamera])

  useEffect(() => {
    continuousMode.current = continuous
    const backendSource = activeSourceId === 'laptop_builtin' || activeSourceId === 'usb_webcam'
    const remoteFallbackSource = activeSourceId === 'remote_phone' && !cameraOn
    if (!continuous || !session || (!cameraOn && !backendSource && !remoteFallbackSource)) return
    const effectSession = session
    const runGeneration = ++streamGeneration.current
    activeStreamRun.current = runGeneration
    let stopped = false
    let active = false
    let pending: Blob | boolean | null = null
    const dispatch = async () => {
      if (stopped || active || !pending || !sessionRef.current) return
      active = true; continuousBusy.current = true
      const submitted = pending; pending = null; latestFrame.current = null
      const requestSession = sessionRef.current
      try {
        const payloadBlob = typeof submitted === 'object' && submitted instanceof Blob ? submitted : undefined
        const response = await streamFrame(apiUrl, requestSession, payloadBlob, 12000, {
          sourceId: activeSourceId,
          sourceVersion: sourceVersion ?? undefined,
          streamRun: runGeneration,
        })
        if (!stopped && runGeneration === streamGeneration.current && sessionRef.current === requestSession && response.result) {
          const result = response.result
          void acceptResult(result, payloadBlob, () => mounted.current && !stopped && runGeneration === streamGeneration.current && sessionRef.current === requestSession)
        }
      } catch (caught) {
        if (!stopped && runGeneration === streamGeneration.current && mounted.current) {
          if (caught instanceof ApiError && caught.status === 401) {
            invalidateSession('La sesión expiró. Empareja el teléfono de nuevo.')
          } else if (caught instanceof ApiError && caught.status === 409) {
            if (isTrueAuthorityLoss(caught.status, caught.message, caught.code)) {
              setLeaseMessage('El control físico fue tomado por otro operador o expiró.')
              setContinuous(false)
              stopCamera()
            } else {
              setError(mapActionableErrorMessage(caught))
            }
          } else {
            setError(mapActionableErrorMessage(caught))
          }
        }
      } finally {
        active = false; continuousBusy.current = false
        if (!stopped && pending) void dispatch()
      }
    }
    const interval = window.setInterval(() => {
      if (stopped) return
      if (backendSource) {
        pending = true
        void dispatch()
      } else {
        const capture = cameraOn ? captureFromVideo() : Promise.resolve(backendPreviewBlobRef.current)
        void capture.then((blob) => {
          if (blob && !stopped) { pending = blob; latestFrame.current = blob; void dispatch() }
        }).catch((caught) => {
          if (stopped || !mounted.current) return
          setError(mapActionableErrorMessage(caught))
          continuousMode.current = false
          setContinuous(false)
          void requestStreamStop(effectSession, { sourceId: activeSourceId, sourceVersion: sourceVersion ?? undefined, streamRun: runGeneration }).catch(() => {})
        })
      }
    }, 500)
    return () => {
      stopped = true
      streamGeneration.current += 1
      pending = null
      latestFrame.current = null
      active = false
      window.clearInterval(interval)
      void requestStreamStop(effectSession, { sourceId: activeSourceId, sourceVersion: sourceVersion ?? undefined, streamRun: runGeneration }).catch(() => {})
      if (activeStreamRun.current === runGeneration) activeStreamRun.current = null
    }
  }, [acceptResult, activeSourceId, apiUrl, cameraOn, continuous, invalidateSession, requestStreamStop, session, sourceVersion, stopCamera])

  const simulation = analysis?.simulated || analysis?.arduino.simulated || Boolean(status?.arduino?.simulated)
  const imageWidth = analysis?.image.width || preview?.width || 1
  const imageHeight = analysis?.image.height || preview?.height || 1
  const preserve = fit === 'cover' ? 'xMidYMid slice' : 'xMidYMid meet'
  const renderedScale = viewerSize.width && viewerSize.height
    ? (fit === 'cover' ? Math.max(viewerSize.width / imageWidth, viewerSize.height / imageHeight) : Math.min(viewerSize.width / imageWidth, viewerSize.height / imageHeight))
    : 1
  const overlayFontSize = Math.max(8, Math.min(64, 14 / Math.max(renderedScale, 0.001)))
  const detections = analysis?.detections || []
  const toolSlots = useMemo(() => {
    const categories = config?.inventory?.categories || []
    return Array.from({ length: 9 }, (_, index) => {
      const cat = categories[index]
      const classId = cat?.class_id || `tool_${index + 1}`
      const name = cat?.display_name || cat?.name || (inventoryExample ? `Ubicación ${index + 1}` : `Casillero ${index + 1}`)
      const location = cat?.location || `Casillero ${index + 1}`
      const isDetected = detections.some((d) => d.class_id === classId || d.name?.toLowerCase() === cat?.name?.toLowerCase())
      return {
        index,
        classId,
        name,
        location,
        isDetected,
        isStored: isDetected,
      }
    })
  }, [config, detections, inventoryExample])

  const detectedCount = toolSlots.filter((t) => t.isStored).length
  const notDetectedCount = toolSlots.length - detectedCount
  const remoteResumeAvailable = activeSourceId === 'remote_phone' && transmitterConnected && Boolean(
    remoteStreamRef.current?.getVideoTracks?.().some((track) => track.readyState !== 'ended') ||
    peerConnectionRef.current?.getReceivers?.().some((receiver) => receiver.track && receiver.track.readyState !== 'ended'),
  )

  const ledItems = useMemo(() => Array.from({ length: 9 }, (_, bit) => {
    const item = inventory.find((candidate, index) => (candidate.led_index ?? candidate.bit ?? index) === bit)
    const ackKnown = Boolean(analysis?.arduino.connected && analysis?.arduino.ack)
    return { bit, name: inventoryExample ? `Ubicación ${bit + 1}` : item?.name || item?.location || `Ubicación ${bit + 1}`, requested: Boolean((analysis?.arduino.requested_mask || 0) & (1 << bit)), confirmed: analysis ? ackKnown ? Boolean((analysis.arduino.confirmed_mask || 0) & (1 << bit)) : null : null }
  }), [analysis, inventory, inventoryExample])

  return <main className="shell">
    <header className="topbar">
      <div><p className="eyebrow">Instrumento local</p><h1>ToolLighthouse</h1></div>
      <div className={`connection connection-${connection}`} aria-live="polite"><i aria-hidden="true" />{connectionLabel(connection)}</div>
    </header>

    <Navigation
      activeTab={activeTab}
      onChangeTab={setActiveTab}
      isDirty={Boolean(config?.is_dirty)}
      hasLocalChanges={hasLocalDraftChanges}
      savedVersion={config?.saved_version}
      activeVersion={config?.active_version}
      task={configStatus?.task}
      onApplyConfig={session?.role === 'admin' ? () => void handleApplyConfig() : undefined}
      applying={applyingConfig}
    />

    {error && activeTab !== 'inspection' && (
      <div className="alert alert-error global-error" role="alert">
        <strong>Atención:</strong> {error}
      </div>
    )}

    {activeTab === 'inspection' && (
      <div className="layout">
        <section className="workspace" aria-label="Captura y análisis">
          {status?.maintenance_mode && (
            <div className="alert alert-warning" role="alert">
              <strong>Modo de mantenimiento activo:</strong> La cámara, captura y análisis están bloqueados hasta que finalice el mantenimiento de Arduino.
            </div>
          )}
          <CameraSelector
            sources={cameraSources}
            webcams={webcams}
            activeSourceId={activeSourceId}
            onSelectSource={handleSelectSource}
            onSelectWebcam={handleSelectWebcam}
            onOpenInviteModal={handleOpenInvite}
            onRevokeTransmitters={handleRevokeTransmitters}
            observedVideo={observedVideo}
            disabled={!canControl || Boolean(status?.maintenance_mode)}
          />
          <div className="section-heading"><div><p className="eyebrow">Escena</p><h2>Inspección fotográfica</h2></div><span className="mode-chip">{simulation ? 'Simulación activa' : analysis?.mode || 'Listo'}</span></div>
          <div ref={viewerRef} className={`viewer ${preview ? 'viewer-ready' : ''}`}>
            {preview ? <svg className="preview-svg" viewBox={`0 0 ${imageWidth} ${imageHeight}`} preserveAspectRatio={preserve} role="img" aria-label="Fotografía con detecciones">
              <image href={preview.url} x="0" y="0" width={imageWidth} height={imageHeight} preserveAspectRatio={preserve} />
              {detections.map((d: Detection) => <g key={`${d.track_id}-${d.class_id}`}>
                <rect className="bbox" x={d.bbox[0]} y={d.bbox[1]} width={d.bbox[2]} height={d.bbox[3]} />
                <text className="bbox-label" style={{ fontSize: `${overlayFontSize}px` }} x={d.bbox[0] + 8} y={Math.max(20, d.bbox[1] + overlayFontSize + 4)}>{d.name || 'Sin identificar'}</text>
              </g>)}
            </svg> : <div className="empty-viewer"><span className="crosshair" aria-hidden="true">＋</span><strong>La bandeja aparecerá aquí</strong><span>Elige una foto o abre la cámara para comenzar.</span></div>}
            <video className={`camera-preview ${cameraOn ? '' : 'camera-hidden'}`} ref={videoRef} muted playsInline aria-label="Vista previa de la cámara" />
            {backendPreviewUrl && (activeSourceId === 'laptop_builtin' || activeSourceId === 'usb_webcam' || (activeSourceId === 'remote_phone' && !cameraOn)) && (
              <img
                src={backendPreviewUrl}
                alt="Vista previa de cámara web"
                className="camera-preview"
                style={{ objectFit: fit }}
              />
            )}
            {busy && <div className="viewer-busy" role="status">Analizando en la laptop…</div>}
          </div>
          <div className="capture-row">
            <label className="button button-primary"><span aria-hidden="true">↥</span> Elegir foto<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; void chooseImage(file) }} /></label>
            <button className="button button-secondary" onClick={() => cameraOn ? stopCamera() : void startCamera()} disabled={!canControl || Boolean(status?.maintenance_mode) || (!cameraOn && cameraSources.find((source) => source.source_id === activeSourceId)?.kind !== 'browser' && !remoteResumeAvailable)}>{cameraOn ? 'Cerrar cámara' : 'Abrir cámara'}</button>
            {cameraOn && <button className="button button-accent button-purple" onClick={() => void captureFromVideo().then(async (blob) => { if (!blob) return; const width = videoRef.current?.videoWidth || 1; const height = videoRef.current?.videoHeight || 1; setPreviewSafely({ blob, url: URL.createObjectURL(blob), width, height }); await stopContinuousForAction(); stopCamera(activeSourceId === 'remote_phone'); await inspect(blob) }).catch((caught) => setError(mapActionableErrorMessage(caught)))} disabled={busy || !canControl || Boolean(status?.maintenance_mode)}>Analizar toma</button>}
            {!cameraOn && <button className="button button-accent button-purple" onClick={() => void inspect(backendPreviewBlob || preview?.blob)} disabled={busy || (!preview && !backendPreviewBlob && activeSourceId !== 'laptop_builtin' && activeSourceId !== 'usb_webcam' && activeSourceId !== 'remote_phone') || !canControl || Boolean(status?.maintenance_mode)}>{busy ? 'Analizando…' : (activeSourceId === 'laptop_builtin' || activeSourceId === 'usb_webcam') ? 'Escanear Cámara Web' : activeSourceId === 'remote_phone' ? 'Escanear Cámara Remota' : 'Analizar foto'}</button>}
          </div>
            <div className="viewer-tools"><span>Vista del overlay</span><button className={fit === 'contain' ? 'selected' : ''} onClick={() => setFit('contain')}>Completa</button><button className={fit === 'cover' ? 'selected' : ''} onClick={() => setFit('cover')}>Recortada</button><label className="continuous-toggle"><input type="checkbox" checked={continuous} onChange={(e) => setContinuous(e.target.checked)} disabled={!session || (!cameraOn && !backendPreviewBlob && activeSourceId !== 'laptop_builtin' && activeSourceId !== 'usb_webcam')} /> Continuo <small>2 FPS</small></label></div>
          {error && <div className="alert alert-error" role="alert"><strong>No se completó la acción.</strong> {error}</div>}
          {leaseMessage && <div className="alert alert-info" role="status">{leaseMessage}</div>}
        </section>

        <aside className="results" aria-label="Resultados de la inspección">
          <section className="panel inventory-3x3-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Inventario Físico</p>
                <h2>Estado de Tablero (3x3)</h2>
              </div>
              <span className="count">
                {analysis ? `${detectedCount} detectada${detectedCount === 1 ? '' : 's'} / ${notDetectedCount} no detectada${notDetectedCount === 1 ? '' : 's'}` : `${toolSlots.length} casilleros`}
              </span>
            </div>
            <div className="inventory-3x3-grid">
              {toolSlots.map((slot) => {
                const cardClass = analysis
                  ? slot.isStored
                    ? 'inventory-slot-card stored'
                    : 'inventory-slot-card missing'
                  : 'inventory-slot-card'
                const dotClass = analysis
                  ? slot.isStored
                    ? 'inventory-status-dot stored'
                    : 'inventory-status-dot missing'
                  : 'inventory-status-dot'
                return (
                  <div key={slot.index} className={cardClass}>
                    <div className="inventory-slot-header">
                      <span className="inventory-slot-name">{slot.name}</span>
                      <span className={dotClass} />
                    </div>
                    <div className="inventory-slot-footer">
                      <span className="inventory-slot-location">{slot.location}</span>
                      {analysis && (
                        <span className={`inventory-slot-status-text ${slot.isStored ? 'stored' : 'missing'}`}>
                          {slot.isStored ? 'DETECTADA EN LECTURA' : 'NO DETECTADA EN ESTA LECTURA'}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="panel result-panel"><div className="panel-heading"><div><p className="eyebrow">Lectura</p><h2>Herramientas detectadas</h2></div><span className="count">{detections.length}</span></div>
            {detections.length ? <div className="detection-list">{detections.map((d) => <article className="detection" key={`${d.track_id}-${d.class_id}`}><div className="detection-mark" aria-hidden="true" /><div><strong>{d.name || 'Sin identificar'}</strong><span>{displayLocation(d.location)} · track {d.track_id} · similitud {formatSimilarity(d.similarity_top1)}</span></div><b>{d.validation === 'simulated' ? 'SIM' : d.class_id}</b></article>)}</div> : <p className="empty-copy">Las lecturas válidas de la fotografía aparecerán aquí. Los objetos ambiguos permanecen sin asignación.</p>}
          </section>

          <section className="panel activity-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Registro Local</p>
                <h2>Actividad Reciente</h2>
              </div>
              <span className="mode-chip">Hoy</span>
            </div>
            <div className="activity-feed-panel">
              {activityLogs.map((log) => (
                <div key={log.id} className="activity-feed-item">
                  <span className={`activity-feed-dot ${log.type}`} />
                  <span className="activity-feed-time">{log.time}</span>
                  <span className="activity-feed-msg">{log.message}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="panel led-panel"><div className="panel-heading"><div><p className="eyebrow">Salida física</p><h2>Mapa de luces</h2></div><span className={`ack ${analysis?.arduino.ack ? 'ack-ok' : ''}`}>{analysis ? analysis.arduino.ack ? analysis.arduino.simulated ? 'ACK simulado' : 'ACK confirmado' : 'Sin confirmación' : 'Sin lectura'}</span></div><div className="led-grid">{ledItems.map((led) => <div className="led-cell" key={led.bit}><span className={`led ${led.confirmed === true ? 'led-confirmed' : led.requested ? 'led-requested' : ''}`} aria-label={`${led.name}: ${led.confirmed === null ? 'sin confirmación' : led.confirmed ? 'confirmada' : led.requested ? 'solicitada' : 'apagada'}`} /><div><strong>{led.name}</strong><span>{led.confirmed === null ? 'Sin confirmación' : led.confirmed ? 'Confirmada' : led.requested ? 'Solicitada' : 'Apagada'}</span></div></div>)}</div><p className="legend"><span className="dot dot-confirmed" /> Confirmada <span className="dot dot-requested" /> Solicitada</p></section>
          {analysis && <section className="panel detail-panel"><div className="panel-heading"><div><p className="eyebrow">Trazabilidad</p><h2>Detalle de lectura</h2></div></div><div className="detail-grid"><span>Frame <strong>{analysis.frame_id.slice(0, 12)}</strong></span><span>Track <strong>{analysis.detections[0]?.track_id ?? '—'}</strong></span><span>Modo <strong>{analysis.mode}</strong></span><span>Validación <strong>{analysis.detections[0]?.validation || 'Sin detecciones'}</strong></span><span>Captura <strong>{formatMetric(analysis.metrics?.capture_fps, 'fps')}</strong></span><span>Presentación <strong>{formatMetric(analysis.metrics?.display_fps, 'fps')}</strong></span><span>Inferencia <strong>{formatMetric(analysis.metrics?.inference_hz, 'Hz')}</strong></span><span>Latencia p50 <strong>{formatMetric(analysis.metrics?.inference_ms_p50, 'ms')}</strong><small>{metricSampleLabel(analysis.metrics?.stages?.embed?.sample_count ?? analysis.metrics?.sample_count)}</small></span><span>Latencia p95 <strong>{formatMetric(analysis.metrics?.inference_ms_p95, 'ms')}</strong><small>{metricSampleLabel(analysis.metrics?.stages?.embed?.sample_count ?? analysis.metrics?.sample_count)}</small></span><span>VRAM <strong>{formatMetric(analysis.metrics?.vram_mb, 'MB', 'No disponible')}</strong></span><span>Backend → ACK <strong>{formatMetric(analysis.metrics?.capture_to_ack_ms, 'ms', analysis.simulated || analysis.arduino.simulated ? 'No disponible en simulación' : 'Sin muestras suficientes')}</strong></span>{Object.entries(analysis.metrics?.stages || analysis.metrics?.stage_ms || {}).map(([stage, metric]) => <span key={stage}>{stage} <strong>{formatMetric(metric?.p50_ms, 'ms')}</strong><small>{metricSampleLabel(metric?.sample_count)}</small></span>)}</div></section>}
          <section className="panel settings-panel"><button className="panel-heading disclosure" onClick={() => setSettingsOpen(!settingsOpen)} aria-expanded={settingsOpen}><div><p className="eyebrow">Conexión</p><h2>Sesión y backend</h2></div><span aria-hidden="true">{settingsOpen ? '⌃' : '⌄'}</span></button>{settingsOpen && <div className="settings-body"><label>URL del backend<input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} disabled={Boolean(session)} placeholder="http://192.168.1.20:8000" /></label><label>ID de este teléfono<input value={deviceId} onChange={(e) => setDeviceId(e.target.value)} disabled={Boolean(session)} /></label><label>Rol de sesión<select value={requestedRole} onChange={(e) => setRequestedRole(e.target.value as 'controller' | 'reader' | 'admin')} disabled={Boolean(session)}><option value="controller">Controlador · cámara y Arduino</option><option value="reader">Lector · solo resultados</option><option value="admin">Administrador · configuración, calibración y hardware</option></select></label><label>{requestedRole === 'admin' ? 'Clave de administrador' : 'Clave de emparejamiento'}<input type="password" value={pairingKey} onChange={(e) => setPairingKey(e.target.value)} disabled={Boolean(session)} placeholder="Configurada en la laptop" /></label>{session ? <button className="button button-danger full" onClick={() => void release()}>Liberar sesión</button> : <button className="button button-accent full" onClick={() => void connect()} disabled={connection === 'checking' || !apiUrl}>{connection === 'checking' ? 'Verificando…' : 'Verificar y conectar'}</button>}{session && session.role === 'admin' && <button className="button button-secondary full" onClick={() => void handleTakeover()} title="Tomar control físico exclusivo como administrador">Tomar Control Físico (Admin Takeover)</button>}{preview && session?.role === 'admin' && <button className="button button-secondary full" onClick={() => void (async () => { setCalibrating(true); setError(''); try { await calibrateBackground(apiUrl, session, preview.blob); const latest = await getConfig(apiUrl, session); mergeReferenceRefresh(latest); setLeaseMessage('Fondo vacío guardado para esta cámara.') } catch (caught) { setError(caught instanceof Error ? caught.message : 'No se pudo guardar el fondo') } finally { setCalibrating(false) } })()} disabled={calibrating}>{calibrating ? 'Guardando fondo…' : 'Usar foto actual como fondo vacío'}</button>}<div className="connection-note"><span className={`status-dot status-${connection}`} />{session ? session.role === 'admin' ? 'Sesión administrativa · Control total del sistema' : session.role === 'reader' ? 'Sesión lectora; no controla el Arduino' : 'Control exclusivo de este teléfono' : 'Sin sesión de control'}{status?.arduino?.simulated && <em>Arduino simulado</em>}</div>{inventory.length > 0 && <p className="inventory-note">{inventoryExample ? 'Inventario de ejemplo' : 'Inventario confirmado'}: {inventory.map((item, index) => item.name || item.location || `LED ${item.led_index ?? item.bit ?? index}`).join(' · ')}</p>}</div>}</section>
        </aside>
      </div>
    )}

    {activeTab === 'tools' && (
      <ToolManager
        apiUrl={apiUrl}
        credentials={session}
        config={config}
        draftConfig={draftConfig}
        onDraftChange={setDraftConfig}
        onConfigUpdated={handleConfigUpdated}
        onServerConfigRefreshed={mergeReferenceRefresh}
        onNavigateTab={setActiveTab}
      />
    )}

    {activeTab === 'calibration' && (
      <CalibrationView
        apiUrl={apiUrl}
        credentials={session}
        config={config}
        draftConfig={draftConfig}
        onDraftChange={setDraftConfig}
        onConfigUpdated={handleConfigUpdated}
        onServerConfigRefreshed={mergeReferenceRefresh}
      />
    )}

    {activeTab === 'arduino' && (
      <ArduinoPanel
        apiUrl={apiUrl}
        credentials={session}
        config={config}
        currentArduinoStatus={status?.arduino}
        maintenanceMode={Boolean(status?.maintenance_mode)}
        onMaintenanceChanged={(inMaintenance, arduino) => {
          setStatus((prev) => prev ? {
            ...prev,
            maintenance_mode: inMaintenance,
            arduino: arduino || prev.arduino,
          } : prev)
        }}
        onStatusUpdated={(s) => setStatus((prev) => prev ? { ...prev, arduino: s } : { arduino: s })}
      />
    )}

    {activeTab === 'system' && (
      <SystemPanel
        apiUrl={apiUrl}
        credentials={session}
        config={config}
        task={configStatus?.task}
        onConfigUpdated={handleConfigUpdated}
        onNavigateTab={setActiveTab}
      />
    )}

    {qrModalOpen && activeInvite && (
      <QRModal
        inviteUrl={activeInvite.invite_url}
        expiresAt={activeInvite.expires_at}
        onClose={() => setQrModalOpen(false)}
        onCancelInvite={handleCancelInvite}
      />
    )}

    <footer><span>ToolLighthouse · análisis en laptop</span><span>Modo continuo requiere HTTPS o localhost · WebRTC activo para streaming</span></footer>
  </main>
}
