import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QRModal } from './QRModal'
import QRCode from 'qrcode'

vi.mock('qrcode', () => ({
  default: {
    toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,mockqrdata'),
  },
}))

describe('QRModal Component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  it('renders modal header, generates QR code offline, and displays remaining time', async () => {
    const onClose = vi.fn()
    const onCancel = vi.fn()
    const expiresAt = Date.now() / 1000 + 180

    render(
      <QRModal
        inviteUrl="https://192.168.1.50:8000/camera#token=secret-token"
        expiresAt={expiresAt}
        onClose={onClose}
        onCancelInvite={onCancel}
      />
    )

    expect(screen.getByText('Cámara Celular (WebRTC LAN)')).toBeInTheDocument()
    expect(screen.getByText(/Escanea este código con tu dispositivo móvil/i)).toBeInTheDocument()

    await waitFor(() => {
      expect(QRCode.toDataURL).toHaveBeenCalledWith(
        'https://192.168.1.50:8000/camera#token=secret-token',
        expect.any(Object)
      )
    })

    const qrImg = screen.getByAltText('Código QR de Invitación')
    expect(qrImg).toHaveAttribute('src', 'data:image/png;base64,mockqrdata')
  })

  it('copies URL to clipboard when copy button is clicked', async () => {
    const onClose = vi.fn()
    const onCancel = vi.fn()
    const expiresAt = Date.now() / 1000 + 180

    render(
      <QRModal
        inviteUrl="https://192.168.1.50:8000/camera#token=secret-token"
        expiresAt={expiresAt}
        onClose={onClose}
        onCancelInvite={onCancel}
      />
    )

    const copyBtn = screen.getByRole('button', { name: /Copiar URL/i })
    fireEvent.click(copyBtn)

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        'https://192.168.1.50:8000/camera#token=secret-token'
      )
    })
  })

  it('calls onCancelInvite when cancel button is clicked', () => {
    const onClose = vi.fn()
    const onCancel = vi.fn()
    const expiresAt = Date.now() / 1000 + 180

    render(
      <QRModal
        inviteUrl="https://192.168.1.50:8000/camera#token=secret-token"
        expiresAt={expiresAt}
        onClose={onClose}
        onCancelInvite={onCancel}
      />
    )

    const cancelBtn = screen.getByRole('button', { name: /Cancelar Invitación/i })
    fireEvent.click(cancelBtn)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('shows expired warning when remaining seconds reach 0', () => {
    const onClose = vi.fn()
    const onCancel = vi.fn()
    const expiredTime = Date.now() / 1000 - 10

    render(
      <QRModal
        inviteUrl="https://192.168.1.50:8000/camera#token=secret-token"
        expiresAt={expiredTime}
        onClose={onClose}
        onCancelInvite={onCancel}
      />
    )

    expect(screen.getByText('Invitación expirada')).toBeInTheDocument()
  })
})
