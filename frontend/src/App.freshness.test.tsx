import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const api = vi.hoisted(() => ({
  createSession: vi.fn(), createWsTicket: vi.fn(), getStatus: vi.fn(), getInventory: vi.fn(), openEvents: vi.fn(), releaseSession: vi.fn(),
  analyzePhoto: vi.fn(), streamFrame: vi.fn(), stopStream: vi.fn(), scanTools: vi.fn(), defaultApiUrl: vi.fn(() => 'http://localhost:8000'),
  getCameraSources: vi.fn(), setActiveCameraSource: vi.fn(), createCameraInvite: vi.fn(), cancelCameraInvite: vi.fn(), revokeCameraTransmitters: vi.fn(),
  takeoverLease: vi.fn(), sessionHeartbeat: vi.fn(), getConfig: vi.fn(), getConfigStatus: vi.fn(), getCalibrationProfiles: vi.fn(),
  captureProfileBackground: vi.fn(), updateProfileRoi: vi.fn(), confirmLegacyProfile: vi.fn(), updateConfig: vi.fn(),
  ApiError: class ApiError extends Error { status = 0 },
}))

vi.mock('./api', () => api)
vi.mock('./videoFreshness', () => ({
  createVideoFreshnessState: () => ({ initialized: false, presentedFrames: null, mediaTime: null }),
  waitForFreshVideoFrame: vi.fn().mockRejectedValue(new Error('El vídeo no ha recibido un fotograma nuevo. Comprueba la cámara.')),
}))

describe('manual capture freshness guard', () => {
  afterEach(() => vi.unstubAllGlobals())

  beforeEach(() => {
    vi.clearAllMocks()
    api.createSession.mockResolvedValue({ session_id: 'fresh-sess', ticket: 'fresh-ticket', role: 'controller' })
    api.createWsTicket.mockResolvedValue({ ticket: 'ws-ticket' })
    api.getStatus.mockResolvedValue({ arduino: { simulated: true } })
    api.getInventory.mockResolvedValue({ categories: [] })
    api.getConfig.mockResolvedValue({ settings: { mode: 'mock' }, inventory: { categories: [] }, saved_version: 1, active_version: 1 })
    api.getConfigStatus.mockResolvedValue({ saved_version: 1, active_version: 1 })
    api.getCameraSources.mockResolvedValue({
      sources: [{ source_id: 'operator_device', name: 'Cámara de este Navegador', kind: 'browser', status: 'active', resolution: { width: 640, height: 480 }, fps: 30, calibration_available: true, requires_operator_action: true }],
      active_source_id: 'operator_device', source_version: 1,
    })
    api.openEvents.mockReturnValue({ close: vi.fn() })
    api.sessionHeartbeat.mockResolvedValue({ lease_held: true })
    api.stopStream.mockResolvedValue({ stopped: true, stream_generation: 1 })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) } })
    Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: vi.fn().mockResolvedValue(undefined) })
  })

  it('shows the frozen-video error and does not call scan', async () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abrir cámara' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Abrir cámara' }))
    const video = await screen.findByLabelText('Vista previa de la cámara') as HTMLVideoElement
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 640 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 480 })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analizar toma' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Analizar toma' }))
    await waitFor(() => expect(screen.getByText(/fotograma nuevo/i)).toBeInTheDocument())
    expect(api.scanTools).not.toHaveBeenCalled()
  })

  it('reattaches the existing remote peer after manual analysis without a new invite', async () => {
    const remoteTrack = { readyState: 'live', stop: vi.fn(), getSettings: vi.fn(() => ({ width: 1280, height: 720, frameRate: 30 })) }
    const remoteStream = {
      getTracks: () => [remoteTrack],
      getVideoTracks: () => [remoteTrack],
    }
    let socket: any
    let peer: any
    class FakeWebSocket {
      static OPEN = 1
      readyState = 1
      onopen: (() => void) | null = null
      onmessage: ((event: { data: string }) => void) | null = null
      onclose: (() => void) | null = null
      send = vi.fn()
      close = vi.fn()
      constructor(url: string) {
        if (url.includes('/v1/camera/signal')) socket = this
        queueMicrotask(() => this.onopen?.())
      }
    }
    class FakePeerConnection {
      remoteDescription: object | null = null
      ontrack: ((event: { streams: unknown[] }) => void) | null = null
      onicecandidate: (() => void) | null = null
      addTransceiver = vi.fn()
      createOffer = vi.fn().mockResolvedValue({ type: 'offer', sdp: 'offer' })
      setLocalDescription = vi.fn().mockResolvedValue(undefined)
      setRemoteDescription = vi.fn().mockResolvedValue(undefined)
      addIceCandidate = vi.fn().mockResolvedValue(undefined)
      close = vi.fn()
      getReceivers = vi.fn(() => [{ track: remoteTrack }])
      constructor() { peer = this }
    }
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
    api.getCameraSources.mockResolvedValue({
      sources: [{ source_id: 'remote_phone', name: 'Teléfono Remoto (WebRTC)', kind: 'webrtc_remote', status: 'active', resolution: { width: 1280, height: 720 }, fps: 30, calibration_available: true, requires_operator_action: false }],
      active_source_id: 'remote_phone', source_version: 4,
    })
    api.scanTools.mockResolvedValue({
      frame_id: 'remote-manual', timestamp: 1, mode: 'mock', simulated: true,
      image: { width: 1280, height: 720, orientation: 'native' }, detections: [],
      arduino: { requested_mask: 0, confirmed_mask: 0, connected: true, simulated: true, ack: true },
    })
    api.streamFrame.mockResolvedValue({ result: {
      frame_id: 'remote-continuous', timestamp: 2, mode: 'mock', simulated: true,
      image: { width: 1280, height: 720, orientation: 'native' }, detections: [],
      arduino: { requested_mask: 0, confirmed_mask: 0, connected: true, simulated: true, ack: true },
    } })
    vi.mocked((await import('./videoFreshness')).waitForFreshVideoFrame).mockResolvedValue(undefined)
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback) => callback(new Blob(['jpeg'], { type: 'image/jpeg' })))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:remote') })

    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(socket?.onmessage).toBeTruthy())
    await act(async () => { socket.onmessage?.({ data: JSON.stringify({ type: 'peer_connected' }) }); await Promise.resolve() })
    await waitFor(() => expect(peer).toBeTruthy())
    await act(async () => { peer.ontrack?.({ streams: [remoteStream] }); await Promise.resolve() })
    const video = await screen.findByLabelText('Vista previa de la cámara') as HTMLVideoElement
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 1280 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 720 })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analizar toma' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Analizar toma' }))
    await waitFor(() => expect(api.scanTools).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abrir cámara' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Abrir cámara' }))
    await waitFor(() => expect(video.srcObject).toBe(remoteStream))
    expect(peer.close).not.toHaveBeenCalled()
    expect(api.createCameraInvite).not.toHaveBeenCalled()
    fireEvent.click(screen.getByLabelText(/Continuo/))
    await waitFor(() => expect(api.streamFrame).toHaveBeenCalled())
    expect(api.createCameraInvite).not.toHaveBeenCalled()
  })
})
