import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const api = vi.hoisted(() => ({
  createSession: vi.fn(), createWsTicket: vi.fn(), getStatus: vi.fn(), getInventory: vi.fn(), openEvents: vi.fn(), releaseSession: vi.fn(), analyzePhoto: vi.fn(), streamFrame: vi.fn(), calibrateBackground: vi.fn(), defaultApiUrl: vi.fn(() => 'http://localhost:8000'), ApiError: class ApiError extends Error { status = 0 },
}))
vi.mock('./api', () => api)

describe('dashboard connection and camera lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.createSession.mockResolvedValue({ session_id: 's1', ticket: 't1', role: 'controller' })
    api.createWsTicket.mockResolvedValue({ ticket: 'ws-t1' })
    api.getStatus.mockResolvedValue({ arduino: { simulated: true } })
    api.getInventory.mockResolvedValue({ categories: [] })
    api.openEvents.mockReturnValue({ close: vi.fn() })
    api.releaseSession.mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn() } })
    Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: vi.fn().mockResolvedValue(undefined) })
  })

  it('attaches and stops the same camera stream after React finishes mounting the video', async () => {
    const tracks = [{ stop: vi.fn() }]
    const stream = { getTracks: () => tracks }
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(stream as unknown as MediaStream)
    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('ID de este teléfono'), { target: { value: 'phone-test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())
    expect(api.createSession).toHaveBeenCalledWith(expect.any(String), expect.any(String), 'phone-test', 'controller')
    expect(api.createWsTicket).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Abrir cámara' }))
    await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled())
    expect(screen.getByLabelText('Vista previa de la cámara')).toBeInTheDocument()
    await waitFor(() => expect((screen.getByLabelText('Vista previa de la cámara') as HTMLVideoElement).srcObject).toBe(stream))
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar cámara' }))
    await waitFor(() => expect(tracks[0].stop).toHaveBeenCalled())
  })

  it('renders reader sessions without enabling controller actions', async () => {
    api.createSession.mockResolvedValue({ session_id: 's2', ticket: 't2', role: 'reader' })
    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'reader' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText('Sesión lectora; no controla el Arduino')).toBeInTheDocument())
    expect(api.createSession).toHaveBeenCalledWith(expect.any(String), expect.any(String), expect.any(String), 'reader')
    expect(screen.getByRole('button', { name: 'Abrir cámara' })).toBeDisabled()
  })

  it('requests a fresh one-use ticket for each websocket reconnect', async () => {
    const states: Array<(state: 'open' | 'closed' | 'error') => void> = []
    api.createWsTicket.mockResolvedValueOnce({ ticket: 'ws-first' }).mockResolvedValueOnce({ ticket: 'ws-second' })
    api.openEvents.mockImplementation((_base: string, _credentials: unknown, _onEvent: unknown, onState: (state: 'open' | 'closed' | 'error') => void) => { states.push(onState); return { close: vi.fn() } })
    render(<StrictMode><App /></StrictMode>)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(states).toHaveLength(1))
    states[0]('closed')
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1700)) })
    await waitFor(() => expect(states).toHaveLength(2))
    expect(api.openEvents.mock.calls[0][1]).toEqual(expect.objectContaining({ wsTicket: 'ws-first' }))
    expect(api.openEvents.mock.calls[1][1]).toEqual(expect.objectContaining({ wsTicket: 'ws-second' }))
  })

  it('stops a permission result that arrives after the session was released', async () => {
    let resolvePermission!: (stream: MediaStream) => void
    const tracks = [{ stop: vi.fn() }]
    const lateStream = { getTracks: () => tracks } as unknown as MediaStream
    vi.mocked(navigator.mediaDevices.getUserMedia).mockReturnValue(new Promise((resolve) => { resolvePermission = resolve }) as Promise<MediaStream>)
    render(<StrictMode><App /></StrictMode>)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Abrir cámara' }))
    await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Liberar sesión' }))
    resolvePermission(lateStream)
    await waitFor(() => expect(tracks[0].stop).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Abrir cámara' })).toBeDisabled()
  })

  it('stops the previous stream when starting the camera again', async () => {
    const firstTrack = { stop: vi.fn() }
    const secondTrack = { stop: vi.fn() }
    const first = { getTracks: () => [firstTrack] }
    const second = { getTracks: () => [secondTrack] }
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce(first as unknown as MediaStream).mockResolvedValueOnce(second as unknown as MediaStream)
    render(<StrictMode><App /></StrictMode>)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Abrir cámara' }))
    await waitFor(() => expect((screen.getByLabelText('Vista previa de la cámara') as HTMLVideoElement).srcObject).toBe(first))
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar cámara' }))
    fireEvent.click(screen.getByRole('button', { name: 'Abrir cámara' }))
    await waitFor(() => expect((screen.getByLabelText('Vista previa de la cámara') as HTMLVideoElement).srcObject).toBe(second))
    expect(firstTrack.stop).toHaveBeenCalled()
  })

  it('renders normalized detection boxes from the real analysis response', async () => {
    const createBitmap = vi.fn().mockResolvedValue({ width: 100, height: 80, close: vi.fn() })
    Object.defineProperty(window, 'createImageBitmap', { configurable: true, value: createBitmap })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback) => callback(new Blob(['jpg'], { type: 'image/jpeg' })))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:test') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    api.analyzePhoto.mockResolvedValue({ frame_id: 'frame-1', timestamp: 1, mode: 'mock', simulated: true, image: { width: 100, height: 80, orientation: 'normalized' }, detections: [{ track_id: 4, bbox: [10, 20, 30, 25], class_id: 'tool_1', name: 'Llave', validation: 'simulated' }], arduino: { requested_mask: 1, confirmed_mask: 1, connected: true, simulated: true, ack: true } })
    render(<StrictMode><App /></StrictMode>)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())
    const file = new File(['photo'], 'photo.jpg', { type: 'image/jpeg' })
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await waitFor(() => expect(screen.getByRole('img', { name: 'Fotografía con detecciones' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Analizar foto' }))
    await waitFor(() => expect(screen.getAllByText('Llave').length).toBeGreaterThanOrEqual(1))
    const rect = document.querySelector('rect.bbox')
    expect(rect?.getAttribute('x')).toBe('10')
    expect(rect?.getAttribute('y')).toBe('20')
    expect(rect?.getAttribute('width')).toBe('30')
  })

  it('renders actual optional metrics without turning missing values into invented confidence or latency', async () => {
    const createBitmap = vi.fn().mockResolvedValue({ width: 100, height: 80, close: vi.fn() })
    Object.defineProperty(window, 'createImageBitmap', { configurable: true, value: createBitmap })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback) => callback(new Blob(['jpg'], { type: 'image/jpeg' })))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:metrics') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    api.analyzePhoto.mockResolvedValue({
      frame_id: 'frame-metrics', timestamp: 1, mode: 'mock', simulated: true,
      image: { width: 100, height: 80, orientation: 'normalized' }, detections: [],
      arduino: { requested_mask: 0, confirmed_mask: 0, connected: true, simulated: true, ack: true },
      metrics: { capture_fps: 30, display_fps: null, inference_hz: 10, inference_ms_p50: 12.5, inference_ms_p95: null, sample_count: 8, vram_mb: null, capture_to_ack_ms: null, stages: { embed: { p50_ms: 12.5, p95_ms: null, sample_count: 8 } } },
    })
    render(<StrictMode><App /></StrictMode>)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [new File(['photo'], 'photo.jpg', { type: 'image/jpeg' })] } })
    await waitFor(() => expect(screen.getByRole('img', { name: 'Fotografía con detecciones' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Analizar foto' }))
    await waitFor(() => expect(screen.getByText('frame-metric')).toBeInTheDocument())
    expect(screen.getByText('30 fps')).toBeInTheDocument()
    expect(screen.getAllByText('Sin muestras suficientes').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('No disponible')).toBeInTheDocument()
    expect(screen.getByText('No disponible en simulación')).toBeInTheDocument()
    expect(screen.getByText('Backend → ACK')).toBeInTheDocument()
    expect(screen.getAllByText('12.5 ms').length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
  })
})
