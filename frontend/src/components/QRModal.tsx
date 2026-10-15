import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { QrCode, X, Copy, Check, Clock, AlertTriangle } from 'lucide-react'

interface QRModalProps {
  inviteUrl: string
  expiresAt: number
  onClose: () => void
  onCancelInvite: () => void
}

export function QRModal({ inviteUrl, expiresAt, onClose, onCancelInvite }: QRModalProps) {
  const [qrDataUrl, setQrDataUrl] = useState<string>('')
  const [copied, setCopied] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(() => Math.max(0, Math.round(expiresAt - Date.now() / 1000)))

  useEffect(() => {
    let active = true
    QRCode.toDataURL(inviteUrl, {
      width: 250,
      margin: 2,
      color: {
        dark: '#1e1b4b',
        light: '#ffffff',
      },
      errorCorrectionLevel: 'M',
    })
      .then((url) => {
        if (active) setQrDataUrl(url)
      })
      .catch((err) => {
        console.error('Error generando QR offline:', err)
      })

    return () => {
      active = false
    }
  }, [inviteUrl])

  useEffect(() => {
    const timer = setInterval(() => {
      const rem = Math.max(0, Math.round(expiresAt - Date.now() / 1000))
      setRemainingSeconds(rem)
      if (rem <= 0) {
        clearInterval(timer)
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [expiresAt])

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(inviteUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      // Fallback
    }
  }

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60)
    const s = secs % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="qr-modal-title">
      <div className="qr-modal-card">
        <div className="qr-modal-header">
          <div className="qr-modal-icon-badge">
            <QrCode size={22} className="text-purple-600" />
          </div>
          <div>
            <h3 id="qr-modal-title" className="qr-modal-title">
              Cámara Celular (WebRTC LAN)
            </h3>
            <p className="qr-modal-subtitle">Escanea este código con tu dispositivo móvil</p>
          </div>
          <button
            type="button"
            className="qr-modal-close"
            onClick={onClose}
            aria-label="Cerrar modal"
          >
            <X size={20} />
          </button>
        </div>

        <div className="qr-modal-body">
          {remainingSeconds <= 0 ? (
            <div className="qr-expired-alert">
              <AlertTriangle size={32} className="text-amber-600 mb-2" />
              <strong>Invitación expirada</strong>
              <p>Genera una nueva invitación para conectar un dispositivo móvil.</p>
            </div>
          ) : qrDataUrl ? (
            <div className="qr-image-wrapper">
              <img src={qrDataUrl} alt="Código QR de Invitación" className="qr-image" />
            </div>
          ) : (
            <div className="qr-loading">Generando código QR seguro offline...</div>
          )}

          <div className="qr-timer-row">
            <Clock size={16} />
            <span>
              {remainingSeconds > 0
                ? `Vence en: ${formatTime(remainingSeconds)}`
                : 'Expirada'}
            </span>
          </div>

          <div className="qr-url-box">
            <span className="qr-url-text">{inviteUrl}</span>
            <button
              type="button"
              className="qr-copy-btn"
              onClick={copyUrl}
              title="Copiar URL"
              aria-label="Copiar URL"
            >
              {copied ? <Check size={16} className="text-green-600" /> : <Copy size={16} />}
            </button>
          </div>

          <p className="qr-security-note">
            Generación 100% local offline. No se envían datos a servicios en la nube ni CDNs externas.
            El token se transmite exclusivamente en el fragmento (#token) y es de un solo uso.
          </p>
        </div>

        <div className="qr-modal-footer">
          <button
            type="button"
            className="button button-danger"
            onClick={() => {
              onCancelInvite()
              onClose()
            }}
          >
            Cancelar Invitación
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={onClose}
          >
            Cerrar
          </button>
        </div>
      </div>
    </div>
  )
}
