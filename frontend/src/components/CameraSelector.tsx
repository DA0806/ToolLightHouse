import { useState } from 'react'
import type { CameraSourceDescriptor } from '../types'
import type { WebCamDevice } from '../api'
import {
  Camera,
  Smartphone,
  Laptop,
  Usb,
  Upload,
  QrCode,
  ShieldAlert,
  CheckCircle2,
  AlertCircle,
  Radio,
} from 'lucide-react'

interface CameraSelectorProps {
  sources: CameraSourceDescriptor[]
  activeSourceId: string
  onSelectSource: (sourceId: string) => Promise<void>
  onOpenInviteModal: () => void
  onRevokeTransmitters: () => Promise<void>
  webcams?: WebCamDevice[]
  onSelectWebcam?: (sourceId: string, device: WebCamDevice) => Promise<void>
  observedVideo?: { width: number; height: number; fps: number | null } | null
  disabled?: boolean
}

export function CameraSelector({
  sources,
  activeSourceId,
  onSelectSource,
  onOpenInviteModal,
  onRevokeTransmitters,
  webcams = [],
  onSelectWebcam,
  observedVideo = null,
  disabled = false,
}: CameraSelectorProps) {
  const [switching, setSwitching] = useState(false)
  const [revoking, setRevoking] = useState(false)

  const activeSource = sources.find((s) => s.source_id === activeSourceId)

  const formatSourceMetrics = (source: CameraSourceDescriptor) => {
    if (source.source_id === activeSourceId && observedVideo && observedVideo.width > 0 && observedVideo.height > 0) {
      const measuredFps = observedVideo.fps !== null && Number.isFinite(observedVideo.fps) && observedVideo.fps > 0
        ? `${Math.round(observedVideo.fps)} FPS medidos`
        : 'FPS medidos: no disponible'
      return `${observedVideo.width}x${observedVideo.height} · ${measuredFps}`
    }
    const width = Number(source.resolution?.width)
    const height = Number(source.resolution?.height)
    const fps = Number(source.fps)
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return 'Resolución no disponible'
    if (!Number.isFinite(fps) || fps < 0) return `${width}x${height} · objetivo`
    return `${width}x${height} · ${fps} FPS objetivo`
  }

  const getSourceIcon = (kind: CameraSourceDescriptor['kind'], sourceId: string) => {
    switch (kind) {
      case 'webrtc':
        return <Smartphone size={18} className="text-purple-600" />
      case 'opencv':
        return sourceId.includes('builtin') ? (
          <Laptop size={18} className="text-blue-600" />
        ) : (
          <Usb size={18} className="text-emerald-600" />
        )
      case 'browser':
        return <Camera size={18} className="text-indigo-600" />
      case 'file':
        return <Upload size={18} className="text-amber-600" />
      default:
        return <Camera size={18} />
    }
  }

  const handleSourceChange = async (newId: string) => {
    if (newId === activeSourceId || switching || disabled) return
    setSwitching(true)
    try {
      await onSelectSource(newId)
    } finally {
      setSwitching(false)
    }
  }

  const handleRevoke = async () => {
    if (revoking || disabled) return
    setRevoking(true)
    try {
      await onRevokeTransmitters()
    } finally {
      setRevoking(false)
    }
  }

  return (
    <div className="camera-selector-container">
      <div className="camera-selector-bar">
        <div className="camera-selector-select-group">
          <label htmlFor="camera-source-select" className="camera-selector-label">
            <Radio size={14} className="text-purple-600 inline mr-1" />
            Fuente de Video:
          </label>
          <div className="camera-select-wrapper">
            <select
              id="camera-source-select"
              aria-label="Fuente de Video"
              value={activeSourceId}
              onChange={(e) => handleSourceChange(e.target.value)}
              disabled={disabled || switching}
              className="camera-source-select"
            >
              {sources.map((s) => (
                <option key={s.source_id} value={s.source_id}>
                  {s.name} ({s.status})
                </option>
              ))}
            </select>
          </div>
        </div>

        {activeSource?.kind === 'opencv' && webcams.length > 0 && onSelectWebcam && (
          <div className="camera-selector-select-group">
            <label htmlFor="opencv-device-select" className="camera-selector-label">Dispositivo:</label>
            <div className="camera-select-wrapper">
              <select
                id="opencv-device-select"
                aria-label="Dispositivo de cámara OpenCV"
                value={webcams.find((device) => device.is_active)?.index ?? ''}
                onChange={(event) => {
                  const device = webcams.find((candidate) => candidate.index === Number(event.target.value))
                  if (device) void onSelectWebcam(activeSource.source_id, device)
                }}
                disabled={disabled || switching}
                className="camera-source-select"
              >
                <option value="">Selecciona una webcam</option>
                {webcams.map((device) => (
                  <option key={device.index} value={device.index}>
                    {device.name} ({device.index})
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {activeSource && (
          <div className="camera-status-pill">
            {activeSource.status === 'active' ? (
              <CheckCircle2 size={14} className="text-green-500" />
            ) : (
              <AlertCircle size={14} className="text-slate-400" />
            )}
            <span className="camera-status-text">
              {activeSource.status === 'active'
                ? formatSourceMetrics(activeSource)
                : activeSource.status}
            </span>
          </div>
        )}

        <div className="camera-actions-group">
          <button
            type="button"
            className="button button-secondary camera-action-btn"
            onClick={onOpenInviteModal}
            disabled={disabled}
            title="Conectar cámara móvil mediante código QR"
          >
            <QrCode size={16} className="text-purple-700" />
            <span>Vincular Celular (QR)</span>
          </button>

          <button
            type="button"
            className="button button-secondary camera-action-btn"
            onClick={handleRevoke}
            disabled={disabled || revoking}
            title="Revocar todos los transmisores móviles activos"
          >
            <ShieldAlert size={16} className="text-rose-600" />
            <span>{revoking ? 'Revocando...' : 'Revocar Remotos'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
