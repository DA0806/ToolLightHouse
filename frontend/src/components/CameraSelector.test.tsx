import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { CameraSelector } from './CameraSelector'
import type { CameraSourceDescriptor } from '../types'

describe('CameraSelector Component', () => {
  const mockSources: CameraSourceDescriptor[] = [
    {
      source_id: 'remote_phone',
      kind: 'webrtc',
      name: 'Teléfono Móvil (WebRTC)',
      status: 'active',
      resolution: { width: 1920, height: 1080 },
      fps: 30,
      calibration_available: true,
      requires_operator_action: false,
    },
    {
      source_id: 'laptop_builtin',
      kind: 'opencv',
      name: 'Cámara Integrada Laptop',
      status: 'standby',
      resolution: { width: 1280, height: 720 },
      fps: 30,
      calibration_available: false,
      requires_operator_action: false,
    },
    {
      source_id: 'usb_webcam',
      kind: 'opencv',
      name: 'Webcam USB Externa',
      status: 'standby',
      resolution: { width: 1920, height: 1080 },
      fps: 30,
      calibration_available: false,
      requires_operator_action: false,
    },
    {
      source_id: 'operator_device',
      kind: 'browser',
      name: 'Cámara de este Navegador',
      status: 'standby',
      resolution: { width: 1280, height: 720 },
      fps: 30,
      calibration_available: false,
      requires_operator_action: true,
    },
    {
      source_id: 'manual_upload',
      kind: 'file',
      name: 'Carga Manual de Fotografía',
      status: 'standby',
      resolution: { width: 0, height: 0 },
      fps: 0,
      calibration_available: false,
      requires_operator_action: true,
    },
  ]

  it('renders all 5 camera sources and status indicator', () => {
    const onSelect = vi.fn().mockResolvedValue(undefined)
    const onInvite = vi.fn()
    const onRevoke = vi.fn().mockResolvedValue(undefined)

    render(
      <CameraSelector
        sources={mockSources}
        activeSourceId="remote_phone"
        onSelectSource={onSelect}
        onOpenInviteModal={onInvite}
        onRevokeTransmitters={onRevoke}
      />
    )

    const select = screen.getByLabelText('Fuente de Video') as HTMLSelectElement
    expect(select).toBeInTheDocument()
    expect(select.options.length).toBe(5)
    expect(select.value).toBe('remote_phone')
    expect(screen.getByText('1920x1080 · 30 FPS objetivo')).toBeInTheDocument()
  })

  it('triggers source change when user selects a different camera', async () => {
    const onSelect = vi.fn().mockResolvedValue(undefined)
    const onInvite = vi.fn()
    const onRevoke = vi.fn().mockResolvedValue(undefined)

    render(
      <CameraSelector
        sources={mockSources}
        activeSourceId="remote_phone"
        onSelectSource={onSelect}
        onOpenInviteModal={onInvite}
        onRevokeTransmitters={onRevoke}
      />
    )

    const select = screen.getByLabelText('Fuente de Video')
    fireEvent.change(select, { target: { value: 'laptop_builtin' } })

    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith('laptop_builtin')
    })
  })

  it('shows measured active stream metrics instead of the backend target', () => {
    render(
      <CameraSelector
        sources={mockSources}
        activeSourceId="remote_phone"
        observedVideo={{ width: 600, height: 400, fps: 15 }}
        onSelectSource={vi.fn().mockResolvedValue(undefined)}
        onOpenInviteModal={vi.fn()}
        onRevokeTransmitters={vi.fn().mockResolvedValue(undefined)}
      />
    )

    expect(screen.getByText('600x400 · 15 FPS medidos')).toBeInTheDocument()
    expect(screen.queryByText('1920x1080 · 30 FPS objetivo')).not.toBeInTheDocument()
  })

  it('triggers invite modal when QR button is clicked', () => {
    const onSelect = vi.fn().mockResolvedValue(undefined)
    const onInvite = vi.fn()
    const onRevoke = vi.fn().mockResolvedValue(undefined)

    render(
      <CameraSelector
        sources={mockSources}
        activeSourceId="remote_phone"
        onSelectSource={onSelect}
        onOpenInviteModal={onInvite}
        onRevokeTransmitters={onRevoke}
      />
    )

    const qrBtn = screen.getByRole('button', { name: /Vincular Celular/i })
    fireEvent.click(qrBtn)
    expect(onInvite).toHaveBeenCalledTimes(1)
  })

  it('triggers transmitter revocation when revoke button is clicked', async () => {
    const onSelect = vi.fn().mockResolvedValue(undefined)
    const onInvite = vi.fn()
    const onRevoke = vi.fn().mockResolvedValue(undefined)

    render(
      <CameraSelector
        sources={mockSources}
        activeSourceId="remote_phone"
        onSelectSource={onSelect}
        onOpenInviteModal={onInvite}
        onRevokeTransmitters={onRevoke}
      />
    )

    const revokeBtn = screen.getByRole('button', { name: /Revocar Remotos/i })
    fireEvent.click(revokeBtn)
    await waitFor(() => {
      expect(onRevoke).toHaveBeenCalledTimes(1)
    })
  })

  it('disables controls when disabled prop is true', () => {
    const onSelect = vi.fn().mockResolvedValue(undefined)
    const onInvite = vi.fn()
    const onRevoke = vi.fn().mockResolvedValue(undefined)

    render(
      <CameraSelector
        sources={mockSources}
        activeSourceId="remote_phone"
        onSelectSource={onSelect}
        onOpenInviteModal={onInvite}
        onRevokeTransmitters={onRevoke}
        disabled={true}
      />
    )

    expect(screen.getByLabelText('Fuente de Video')).toBeDisabled()
    expect(screen.getByRole('button', { name: /Vincular Celular/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Revocar Remotos/i })).toBeDisabled()
  })
})
