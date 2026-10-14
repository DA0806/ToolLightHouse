import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { analyzePhoto, ApiError, calibrateBackground, createSession, createWsTicket, defaultApiUrl, getInventory, getStatus, openEvents, releaseSession, streamFrame, type Credentials } from './api'
import type { AnalysisResponse, ConnectionState, Detection, InventoryItem, StatusResponse, WsEvent } from './types'
import './styles.css'

const DEVICE_KEY = 'toollighthouse.device-id'
const URL_KEY = 'toollighthouse.api-url'
const MAX_RECONNECT_ATTEMPTS = 5

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
  const [apiUrl, setApiUrl] = useStoredValue(URL_KEY, defaultApiUrl())
  const [deviceId, setDeviceId] = useStoredValue(DEVICE_KEY, `phone-${Math.random().toString(36).slice(2, 8)}`)
  const [pairingKey, setPairingKey] = useState('')
  const [requestedRole, setRequestedRole] = useState<'controller' | 'reader'>('controller')
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
  const continuousMode = useRef(false)
  const previewUrl = useRef<string | null>(null)
  const viewerRef = useRef<HTMLDivElement>(null)
  const invalidateSessionRef = useRef<(message: string) => void>(() => undefined)

  const canControl = session?.role === 'controller'

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
  }, [preview, setPreviewSafely])

  const handleEvent = useCallback((event: WsEvent, isCurrent: () => boolean = () => true) => {
    const result = eventResult(event)
    if (continuousMode.current && event.type === 'detection') return
    if (result && sessionRef.current && result.frame_id) void acceptResult(result, undefined, isCurrent)
    if (event.type === 'system' && event.event === 'controller_released') setLeaseMessage('El control fue liberado por el servidor.')
    const ack = event.arduino || (event.requested_mask !== undefined ? event : undefined)
    if (event.type === 'arduino_ack' && ack && isCurrent()) setAnalysis((current) => current ? { ...current, arduino: ack as AnalysisResponse['arduino'] } : current)
  }, [acceptResult])

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
              if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) {
                invalidateSessionRef.current('La sesión expiró. Empareja el teléfono de nuevo.')
              } else if (mounted.current) connectSocket(credentials)
            })
          }, 1500)
        }
      })
    }).catch((caught) => {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) invalidateSessionRef.current('La sesión expiró. Empareja el teléfono de nuevo.')
      else if (mounted.current) setError(caught instanceof Error ? caught.message : 'No se pudo abrir el canal de eventos')
    })
  }, [apiUrl, handleEvent])

  const connect = async () => {
    setError(''); setLeaseMessage(''); setConnection('checking')
    let created: Credentials | null = null
    try {
      const response = await createSession(apiUrl, pairingKey, deviceId, requestedRole)
      created = { sessionId: response.session_id, ticket: response.ticket, role: requestedRole }
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
      connectSocket(credentials)
      setConnection('connected')
    } catch (caught) {
      if (created) void releaseSession(apiUrl, created).catch(() => undefined)
      sessionRef.current = null
      setSession(null)
      setConnection('error')
      setError(caught instanceof ApiError && caught.status === 409 ? 'Otro teléfono ya controla el Arduino. Libera esa sesión antes de conectar.' : caught instanceof Error ? caught.message : 'No se pudo conectar')
    }
  }

  const stopCamera = useCallback(() => {
    cameraRequestGeneration.current += 1
    streamGeneration.current += 1
    latestFrame.current = null
    continuousBusy.current = false
    cameraStream.current?.getTracks().forEach((track) => track.stop())
    cameraStream.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setCameraOn(false); setContinuous(false)
  }, [])

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
    setSession(null); setStatus(null); setInventory([]); setInventoryExample(false); setAnalysis(null); setConnection('error'); setLeaseMessage(message)
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
    sceneGeneration.current += 1
    setContinuous(false); latestFrame.current = null
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
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth; canvas.height = video.videoHeight
    canvas.getContext('2d')?.drawImage(video, 0, 0)
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
  }

  const inspect = async (blob = preview?.blob) => {
    if (!session) { setError('Conecta una sesión de controlador antes de analizar.'); return }
    if (!blob) { setError('Selecciona una imagen o inicia la cámara.'); return }
    const requestSession = session
    const requestSessionGeneration = sessionGeneration.current
    const requestSceneGeneration = sceneGeneration.current
    setBusy(true); setError('')
    try {
      const result = await analyzePhoto(apiUrl, requestSession, blob)
      if (mounted.current && sessionRef.current === requestSession && sessionGeneration.current === requestSessionGeneration && sceneGeneration.current === requestSceneGeneration) void acceptResult(result, blob, () => mounted.current && sessionRef.current === requestSession && sessionGeneration.current === requestSessionGeneration && sceneGeneration.current === requestSceneGeneration)
    }
    catch (caught) {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) {
        invalidateSession('La sesión dejó de tener control. Vuelve a emparejar el teléfono.')
      }
      setError(caught instanceof Error ? caught.message : 'No se pudo analizar la foto')
    } finally { setBusy(false) }
  }

  const startCamera = async () => {
    if (!canControl) { setError('El modo lector puede consultar resultados, pero no controla la cámara ni el Arduino.'); return }
    const requestGeneration = ++cameraRequestGeneration.current
    cameraStream.current?.getTracks().forEach((track) => track.stop())
    cameraStream.current = null
    setCameraOn(false)
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      if (!mounted.current || requestGeneration !== cameraRequestGeneration.current || !sessionRef.current || sessionRef.current.role !== 'controller') {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      cameraStream.current = stream; setCameraOn(true)
    } catch { setError('La cámara requiere HTTPS o localhost y permiso del navegador. También puedes elegir una foto.') }
  }

  useEffect(() => {
    if (!cameraOn || !cameraStream.current || !videoRef.current) return
    videoRef.current.srcObject = cameraStream.current
    void videoRef.current.play().catch(() => undefined)
  }, [cameraOn])

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
        if (mounted.current && sessionRef.current === current) setStatus(next)
      }).catch((caught) => {
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) invalidateSession('La sesión expiró. Empareja el teléfono de nuevo.')
      })
    }, 2000)
    return () => window.clearInterval(poll)
  }, [apiUrl, invalidateSession, session])

  useEffect(() => {
    continuousMode.current = continuous
    if (!continuous || !cameraOn || !session) return
    const runGeneration = ++streamGeneration.current
    let stopped = false
    let active = false
    let pending: Blob | null = null
    const dispatch = async () => {
      if (stopped || active || !pending || !sessionRef.current) return
      active = true; continuousBusy.current = true
      const submitted = pending; pending = null; latestFrame.current = null
      const requestSession = sessionRef.current
      try {
        const response = await streamFrame(apiUrl, requestSession, submitted)
        if (!stopped && runGeneration === streamGeneration.current && sessionRef.current === requestSession && response.result) {
          const result = response.result
          void acceptResult(result, submitted, () => mounted.current && !stopped && runGeneration === streamGeneration.current && sessionRef.current === requestSession)
        }
      } catch (caught) {
        if (!stopped && runGeneration === streamGeneration.current && mounted.current) {
          if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) {
            sessionRef.current = null; setSession(null); setConnection('error'); setLeaseMessage('La sesión expiró. Empareja el teléfono de nuevo.'); stopCamera()
          } else setError(caught instanceof Error ? caught.message : 'El modo continuo perdió la conexión')
        }
      } finally {
        active = false; continuousBusy.current = false
        if (!stopped && pending) void dispatch()
      }
    }
    const interval = window.setInterval(() => {
      if (stopped) return
      void captureFromVideo().then((blob) => { if (blob && !stopped) { pending = blob; latestFrame.current = blob; void dispatch() } })
    }, 900)
    return () => { stopped = true; streamGeneration.current += 1; pending = null; latestFrame.current = null; active = false; window.clearInterval(interval) }
  }, [acceptResult, apiUrl, cameraOn, continuous, session, stopCamera])

  const simulation = analysis?.simulated || analysis?.arduino.simulated || Boolean(status?.arduino?.simulated)
  const imageWidth = analysis?.image.width || preview?.width || 1
  const imageHeight = analysis?.image.height || preview?.height || 1
  const preserve = fit === 'cover' ? 'xMidYMid slice' : 'xMidYMid meet'
  const renderedScale = viewerSize.width && viewerSize.height
    ? (fit === 'cover' ? Math.max(viewerSize.width / imageWidth, viewerSize.height / imageHeight) : Math.min(viewerSize.width / imageWidth, viewerSize.height / imageHeight))
    : 1
  const overlayFontSize = Math.max(8, Math.min(64, 14 / Math.max(renderedScale, 0.001)))
  const detections = analysis?.detections || []
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

    <div className="layout">
      <section className="workspace" aria-label="Captura y análisis">
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
          {busy && <div className="viewer-busy" role="status">Analizando en la laptop…</div>}
        </div>
        <div className="capture-row">
          <label className="button button-primary"><span aria-hidden="true">↥</span> Elegir foto<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={(event) => void chooseImage(event.target.files?.[0])} /></label>
          <button className="button button-secondary" onClick={() => cameraOn ? stopCamera() : void startCamera()} disabled={!canControl}>{cameraOn ? 'Cerrar cámara' : 'Abrir cámara'}</button>
          {cameraOn && <button className="button button-accent" onClick={() => void captureFromVideo().then((blob) => { if (!blob) return; const width = videoRef.current?.videoWidth || 1; const height = videoRef.current?.videoHeight || 1; setPreviewSafely({ blob, url: URL.createObjectURL(blob), width, height }); stopCamera(); void inspect(blob) })} disabled={busy || !canControl}>Analizar toma</button>}
          {!cameraOn && <button className="button button-accent" onClick={() => void inspect()} disabled={busy || !preview || !canControl}>{busy ? 'Analizando…' : 'Analizar foto'}</button>}
        </div>
        <div className="viewer-tools"><span>Vista del overlay</span><button className={fit === 'contain' ? 'selected' : ''} onClick={() => setFit('contain')}>Completa</button><button className={fit === 'cover' ? 'selected' : ''} onClick={() => setFit('cover')}>Recortada</button><label className="continuous-toggle"><input type="checkbox" checked={continuous} onChange={(e) => setContinuous(e.target.checked)} disabled={!cameraOn || !session} /> Continuo <small>experimental</small></label></div>
        {error && <div className="alert alert-error" role="alert"><strong>No se completó la acción.</strong> {error}</div>}
        {leaseMessage && <div className="alert alert-info" role="status">{leaseMessage}</div>}
      </section>

      <aside className="results" aria-label="Resultados de la inspección">
        <section className="panel result-panel"><div className="panel-heading"><div><p className="eyebrow">Lectura</p><h2>Herramientas detectadas</h2></div><span className="count">{detections.length}</span></div>
          {detections.length ? <div className="detection-list">{detections.map((d) => <article className="detection" key={`${d.track_id}-${d.class_id}`}><div className="detection-mark" aria-hidden="true" /><div><strong>{d.name || 'Sin identificar'}</strong><span>{displayLocation(d.location)} · track {d.track_id} · similitud {formatSimilarity(d.similarity_top1)}</span></div><b>{d.validation === 'simulated' ? 'SIM' : d.class_id}</b></article>)}</div> : <p className="empty-copy">Las lecturas válidas de la fotografía aparecerán aquí. Los objetos ambiguos permanecen sin asignación.</p>}
        </section>
        <section className="panel led-panel"><div className="panel-heading"><div><p className="eyebrow">Salida física</p><h2>Mapa de luces</h2></div><span className={`ack ${analysis?.arduino.ack ? 'ack-ok' : ''}`}>{analysis ? analysis.arduino.ack ? analysis.arduino.simulated ? 'ACK simulado' : 'ACK confirmado' : 'Sin confirmación' : 'Sin lectura'}</span></div><div className="led-grid">{ledItems.map((led) => <div className="led-cell" key={led.bit}><span className={`led ${led.confirmed === true ? 'led-confirmed' : led.requested ? 'led-requested' : ''}`} aria-label={`${led.name}: ${led.confirmed === null ? 'sin confirmación' : led.confirmed ? 'confirmada' : led.requested ? 'solicitada' : 'apagada'}`} /><div><strong>{led.name}</strong><span>{led.confirmed === null ? 'Sin confirmación' : led.confirmed ? 'Confirmada' : led.requested ? 'Solicitada' : 'Apagada'}</span></div></div>)}</div><p className="legend"><span className="dot dot-confirmed" /> Confirmada <span className="dot dot-requested" /> Solicitada</p></section>
        {analysis && <section className="panel detail-panel"><div className="panel-heading"><div><p className="eyebrow">Trazabilidad</p><h2>Detalle de lectura</h2></div></div><div className="detail-grid"><span>Frame <strong>{analysis.frame_id.slice(0, 12)}</strong></span><span>Track <strong>{analysis.detections[0]?.track_id ?? '—'}</strong></span><span>Modo <strong>{analysis.mode}</strong></span><span>Validación <strong>{analysis.detections[0]?.validation || 'Sin detecciones'}</strong></span><span>Captura <strong>{formatMetric(analysis.metrics?.capture_fps, 'fps')}</strong></span><span>Presentación <strong>{formatMetric(analysis.metrics?.display_fps, 'fps')}</strong></span><span>Inferencia <strong>{formatMetric(analysis.metrics?.inference_hz, 'Hz')}</strong></span><span>Latencia p50 <strong>{formatMetric(analysis.metrics?.inference_ms_p50, 'ms')}</strong><small>{metricSampleLabel(analysis.metrics?.stages?.embed?.sample_count ?? analysis.metrics?.sample_count)}</small></span><span>Latencia p95 <strong>{formatMetric(analysis.metrics?.inference_ms_p95, 'ms')}</strong><small>{metricSampleLabel(analysis.metrics?.stages?.embed?.sample_count ?? analysis.metrics?.sample_count)}</small></span><span>VRAM <strong>{formatMetric(analysis.metrics?.vram_mb, 'MB', 'No disponible')}</strong></span><span>Backend → ACK <strong>{formatMetric(analysis.metrics?.capture_to_ack_ms, 'ms', analysis.simulated || analysis.arduino.simulated ? 'No disponible en simulación' : 'Sin muestras suficientes')}</strong></span>{Object.entries(analysis.metrics?.stages || analysis.metrics?.stage_ms || {}).map(([stage, metric]) => <span key={stage}>{stage} <strong>{formatMetric(metric?.p50_ms, 'ms')}</strong><small>{metricSampleLabel(metric?.sample_count)}</small></span>)}</div></section>}
        <section className="panel settings-panel"><button className="panel-heading disclosure" onClick={() => setSettingsOpen(!settingsOpen)} aria-expanded={settingsOpen}><div><p className="eyebrow">Conexión</p><h2>Sesión y backend</h2></div><span aria-hidden="true">{settingsOpen ? '⌃' : '⌄'}</span></button>{settingsOpen && <div className="settings-body"><label>URL del backend<input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} disabled={Boolean(session)} placeholder="http://192.168.1.20:8000" /></label><label>ID de este teléfono<input value={deviceId} onChange={(e) => setDeviceId(e.target.value)} disabled={Boolean(session)} /></label><label>Rol de sesión<select value={requestedRole} onChange={(e) => setRequestedRole(e.target.value as 'controller' | 'reader')} disabled={Boolean(session)}><option value="controller">Controlador · cámara y Arduino</option><option value="reader">Lector · solo resultados</option></select></label><label>Clave de emparejamiento<input type="password" value={pairingKey} onChange={(e) => setPairingKey(e.target.value)} disabled={Boolean(session)} placeholder="Configurada en la laptop" /></label>{session ? <button className="button button-danger full" onClick={() => void release()}>Liberar sesión</button> : <button className="button button-accent full" onClick={() => void connect()} disabled={connection === 'checking' || !apiUrl}>{connection === 'checking' ? 'Verificando…' : 'Verificar y conectar'}</button>}{preview && canControl && <button className="button button-secondary full" onClick={() => void (async () => { setCalibrating(true); setError(''); try { await calibrateBackground(apiUrl, session, preview.blob); setLeaseMessage('Fondo vacío guardado para esta cámara.') } catch (caught) { setError(caught instanceof Error ? caught.message : 'No se pudo guardar el fondo') } finally { setCalibrating(false) } })()} disabled={calibrating}>{calibrating ? 'Guardando fondo…' : 'Usar foto actual como fondo vacío'}</button>}<div className="connection-note"><span className={`status-dot status-${connection}`} />{session ? session.role === 'reader' ? 'Sesión lectora; no controla el Arduino' : 'Control exclusivo de este teléfono' : 'Sin sesión de control'}{status?.arduino?.simulated && <em>Arduino simulado</em>}</div>{inventory.length > 0 && <p className="inventory-note">{inventoryExample ? 'Inventario de ejemplo' : 'Inventario confirmado'}: {inventory.map((item, index) => item.name || item.location || `LED ${item.led_index ?? item.bit ?? index}`).join(' · ')}</p>}</div>}</section>
      </aside>
    </div>
    <footer><span>ToolLighthouse · análisis en laptop</span><span>Modo continuo requiere HTTPS o localhost · WebRTC queda preparado para una fase posterior</span></footer>
  </main>
}
