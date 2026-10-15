import { useEffect, useRef, useState, useCallback } from 'react'
import { Smartphone, Video, VideoOff, Wifi, WifiOff, AlertTriangle, ShieldCheck } from 'lucide-react'
import { defaultApiUrl, websocketUrl } from '../api'

function invitationTokenFromLocation(): string {
  const hash = window.location.hash
  if (hash.startsWith('#token=')) {
    try { return decodeURIComponent(hash.slice(7)) } catch { return '' }
  }
  return new URLSearchParams(window.location.search).get('token') || ''
}

export function RemoteCameraView() {
  const [token] = useState<string>(() => invitationTokenFromLocation())
  const [transmitterId, setTransmitterId] = useState<string>('')
  const [connectionStatus, setConnectionStatus] = useState<string>('Inicializando...')
  const [iceState, setIceState] = useState<string>('new')
  const [isFallbackMode, setIsFallbackMode] = useState<boolean>(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fallbackIntervalRef = useRef<number | null>(null)
  const wakeLockRef = useRef<any>(null)

  const transmitterTicketRef = useRef<string>('')
  const transmitterIdRef = useRef<string>('')
  const pendingCandidatesRef = useRef<RTCIceCandidateInit[]>([])
  const isUploadingRef = useRef<boolean>(false)

  // Consume the invitation atomically so React StrictMode cannot erase the token
  // before the second effect setup reads it.
  useEffect(() => {
    if (token) {
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, '', window.location.pathname)
      }
    } else {
      setErrorMsg('No se proporcionó un token de invitación válido (#token=...)')
    }
  }, [token])

  // 2. Screen Wake Lock (Supported-only)
  useEffect(() => {
    if ('wakeLock' in navigator) {
      navigator.wakeLock
        .request('screen')
        .then((lock) => {
          wakeLockRef.current = lock
        })
        .catch(() => {
          // Wake lock rejected or unsupported
        })
    }
    return () => {
      if (wakeLockRef.current) {
        wakeLockRef.current.release().catch(() => {})
      }
    }
  }, [])

  // 3. Fallback JPEG Upload to FastAPI (strictly 1 in-flight, ~2 FPS default bounded)
  const startFallbackUploader = useCallback(
    (txId: string, tok: string) => {
      if (fallbackIntervalRef.current) return
      setIsFallbackMode(true)
      setConnectionStatus('Transmitiendo vía Fallback HTTP (JPEG ~2 FPS)')

      const canvas = document.createElement('canvas')
      const ctx = canvas.getContext('2d')

      fallbackIntervalRef.current = window.setInterval(async () => {
        if (isUploadingRef.current || !videoRef.current || !ctx || videoRef.current.readyState < 2) return
        isUploadingRef.current = true
        try {
          const video = videoRef.current
          const vw = video.videoWidth || 640
          const vh = video.videoHeight || 480
          const maxW = 1280
          const maxH = 720
          let dw = vw
          let dh = vh
          if (dw > maxW || dh > maxH) {
            const scale = Math.min(maxW / dw, maxH / dh)
            dw = Math.round(dw * scale)
            dh = Math.round(dh * scale)
          }
          canvas.width = dw
          canvas.height = dh
          ctx.drawImage(video, 0, 0, dw, dh)

          canvas.toBlob(
            async (blob) => {
              if (!blob) {
                isUploadingRef.current = false
                return
              }
              const formData = new FormData()
              formData.append('file', blob, 'frame.jpg')
              try {
                const activeToken = transmitterTicketRef.current || tok
                const res = await fetch(`${defaultApiUrl()}/v1/camera/frame-upload`, {
                  method: 'POST',
                  headers: {
                    'X-Transmitter-Id': txId,
                    'X-Transmitter-Token': activeToken,
                  },
                  body: formData,
                })
                if (!res.ok && res.status === 403) {
                  setErrorMsg('Transmisión revocada o lease expirado')
                  if (fallbackIntervalRef.current) {
                    clearInterval(fallbackIntervalRef.current)
                    fallbackIntervalRef.current = null
                  }
                }
              } catch {
                // Retry on next tick
              } finally {
                isUploadingRef.current = false
              }
            },
            'image/jpeg',
            0.8
          )
        } catch {
          isUploadingRef.current = false
        }
      }, 500) // 500ms = 2 FPS bounded
    },
    []
  )

  // 4. Native WebRTC Signaling Connection. Authentication is performed before
  // requesting camera permission so an invalid or expired invite cannot trigger
  // getUserMedia.
  useEffect(() => {
    if (!token) return

    const wsUrl = websocketUrl(defaultApiUrl(), '/v1/camera/signal')
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    let active = true
    let fallbackTimer: number | null = null
    let pendingOffer: RTCSessionDescriptionInit | null = null

    const addLocalStream = (stream: MediaStream) => {
      stream.getTracks().forEach((track) => {
        if (!pcRef.current) return
        pcRef.current.addTrack(track, stream)
      })
    }

    const answerPendingOffer = async () => {
      if (!pendingOffer || !pcRef.current || !streamRef.current) return
      const offer = pendingOffer
      pendingOffer = null
      await pcRef.current.setRemoteDescription(new RTCSessionDescription(offer))
      for (const candidate of pendingCandidatesRef.current.splice(0)) {
        await pcRef.current.addIceCandidate(new RTCIceCandidate(candidate)).catch(() => {})
      }
      const answer = await pcRef.current.createAnswer()
      await pcRef.current.setLocalDescription(answer)
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'answer', sdp: answer }))
      }
    }

    const requestCameraAfterAuth = async () => {
      if (streamRef.current) return
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1280, max: 1920 },
            height: { ideal: 720, max: 1080 },
          },
          audio: false,
        })
        if (!active) {
          stream.getTracks().forEach((track) => track.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          void videoRef.current.play().catch(() => {})
        }
        addLocalStream(stream)
        await answerPendingOffer()
      } catch (err: any) {
        if (active) setErrorMsg(`Acceso a cámara denegado o no disponible: ${err.message || err}`)
      }
    }

    ws.onopen = () => {
      setConnectionStatus('Autenticando señalización...')
      ws.send(JSON.stringify(
        transmitterIdRef.current && transmitterTicketRef.current
          ? {
              type: 'reconnect_transmitter',
              transmitter_id: transmitterIdRef.current,
              transmitter_ticket: transmitterTicketRef.current,
            }
          : { type: 'auth_transmitter', invite_token: token }
      ))
    }

    ws.onmessage = async (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'auth_ok') {
          setConnectionStatus('Vinculado. Esperando operador...')
          if (typeof msg.transmitter_id === 'string' && msg.transmitter_id) {
            setTransmitterId(msg.transmitter_id)
            transmitterIdRef.current = msg.transmitter_id
          }
          if (msg.transmitter_ticket) {
            transmitterTicketRef.current = msg.transmitter_ticket
          }
          if (!transmitterIdRef.current || !transmitterTicketRef.current) {
            setErrorMsg('El backend no devolvió una credencial de transmisor válida')
            return
          }

          // Setup RTCPeerConnection before requesting camera permission. Offers
          // and candidates can arrive while the user is deciding on permission;
          // they are queued until the local track and remote description exist.
          if (pcRef.current) pcRef.current.close()
          const pc = new RTCPeerConnection({ iceServers: [] })
          pcRef.current = pc
          pendingCandidatesRef.current = []
          if (streamRef.current) addLocalStream(streamRef.current)

          pc.onicecandidate = (event) => {
            if (event.candidate && ws.readyState === WebSocket.OPEN) {
              ws.send(
                JSON.stringify({
                  type: 'candidate',
                  candidate: event.candidate,
                })
              )
            }
          }

          pc.oniceconnectionstatechange = () => {
            const state = pc.iceConnectionState
            setIceState(state)
            if (state === 'connected' || state === 'completed') {
              setConnectionStatus('Transmisión WebRTC P2P Activa')
              if (fallbackTimer) clearTimeout(fallbackTimer)
            } else if (state === 'failed' || state === 'disconnected') {
              setConnectionStatus('P2P degradado. Iniciando fallback...')
              if (transmitterIdRef.current && transmitterTicketRef.current) {
                startFallbackUploader(transmitterIdRef.current, transmitterTicketRef.current)
              }
            }
          }

          // Arm fallback timer if peer connection does not connect within 7s
          fallbackTimer = window.setTimeout(() => {
            if (pc.iceConnectionState !== 'connected' && pc.iceConnectionState !== 'completed') {
              if (transmitterIdRef.current && transmitterTicketRef.current) {
                startFallbackUploader(transmitterIdRef.current, transmitterTicketRef.current)
              }
            }
          }, 7000)
          void requestCameraAfterAuth()
        } else if (msg.type === 'offer') {
          if (!pcRef.current || !streamRef.current) {
            pendingOffer = msg.sdp as RTCSessionDescriptionInit
          } else {
            pendingOffer = msg.sdp as RTCSessionDescriptionInit
            await answerPendingOffer()
          }
        } else if (msg.type === 'candidate') {
          if (pcRef.current && msg.candidate) {
            if (pcRef.current.remoteDescription) {
              await pcRef.current.addIceCandidate(new RTCIceCandidate(msg.candidate)).catch(() => {})
            } else {
              pendingCandidatesRef.current.push(msg.candidate as RTCIceCandidateInit)
            }
          }
        } else if (msg.type === 'disconnect') {
          setErrorMsg('El operador cerró la sesión o cambió de fuente')
          pcRef.current?.close()
        } else if (msg.type === 'error') {
          setErrorMsg(String(msg.message || 'La invitación no fue aceptada'))
        }
      } catch (err: any) {
        console.error('Signaling error:', err)
      }
    }

    ws.onerror = () => {
      setConnectionStatus('Error de websocket. Intentando fallback...')
      // An invitation token is single-use and is only valid for signaling. Never
      // send it to the frame-upload endpoint before the backend has returned a
      // transmitter credential after auth_ok.
      if (transmitterIdRef.current && transmitterTicketRef.current) {
        startFallbackUploader(transmitterIdRef.current, transmitterTicketRef.current)
      } else {
        setConnectionStatus('No se pudo autenticar el transmisor')
      }
    }

    ws.onclose = () => {
      if (!errorMsg) {
        setConnectionStatus('Señalización cerrada')
      }
      if (transmitterIdRef.current && transmitterTicketRef.current) {
        startFallbackUploader(transmitterIdRef.current, transmitterTicketRef.current)
      }
    }

    return () => {
      active = false
      if (fallbackTimer) clearTimeout(fallbackTimer)
      if (fallbackIntervalRef.current) {
        clearInterval(fallbackIntervalRef.current)
        fallbackIntervalRef.current = null
      }
      if (pcRef.current) {
        pcRef.current.close()
        pcRef.current = null
      }
      pendingCandidatesRef.current = []
      pendingOffer = null
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop())
        streamRef.current = null
      }
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close()
      }
    }
  }, [token, startFallbackUploader])

  return (
    <div className="remote-camera-container">
      <div className="remote-camera-card">
        <Smartphone size={46} className="remote-camera-icon animate-pulse" />
        <h2 className="remote-camera-title">Cámara Móvil Vinculada</h2>
        <p className="remote-camera-subtitle">Transmisión en vivo al dashboard del operador.</p>

        {errorMsg ? (
          <div className="remote-camera-error">
            <AlertTriangle size={24} className="mb-1" />
            <p>{errorMsg}</p>
          </div>
        ) : (
          <>
            <div className="remote-camera-viewport">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="remote-camera-video"
              />
              <div className="remote-camera-status-overlay">
                {isFallbackMode ? (
                  <span className="badge badge-warning">Fallback JPEG</span>
                ) : iceState === 'connected' ? (
                  <span className="badge badge-success">P2P LAN</span>
                ) : (
                  <span className="badge badge-neutral">{iceState}</span>
                )}
              </div>
            </div>

            <div className="remote-camera-info-row">
              <div className="flex items-center gap-2">
                {isFallbackMode ? (
                  <WifiOff size={16} className="text-amber-400" />
                ) : (
                  <Wifi size={16} className="text-green-400" />
                )}
                <span className="text-sm font-medium">{connectionStatus}</span>
              </div>
              <div className="flex items-center gap-1 text-slate-400 text-xs">
                <ShieldCheck size={14} className="text-purple-400" />
                <span>LAN Offline</span>
              </div>
            </div>

            <p className="remote-camera-note">
              Mantén esta pantalla encendida mientras realizas la inspección.
            </p>
          </>
        )}
      </div>
    </div>
  )
}
