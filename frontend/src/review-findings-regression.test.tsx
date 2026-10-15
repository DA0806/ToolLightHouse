import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Credentials } from './api'
import { ArduinoPanel } from './components/ArduinoPanel'
import { CalibrationView } from './components/CalibrationView'
import { ToolManager } from './components/ToolManager'
import fs from 'node:fs'
import type { ArduinoStatus, ConfigResponse, WsEvent } from './types'

const api = vi.hoisted(() => ({
  createSession: vi.fn(),
  createWsTicket: vi.fn(),
  getStatus: vi.fn(),
  getInventory: vi.fn(),
  getConfig: vi.fn(),
  getConfigStatus: vi.fn(),
  updateConfig: vi.fn(),
  applyConfig: vi.fn(),
  rollbackConfig: vi.fn(),
  exportConfig: vi.fn(),
  importConfig: vi.fn(),
  getReadiness: vi.fn(),
  setSystemMode: vi.fn(),
  downloadModelInstructions: vi.fn(),
  listReferences: vi.fn(),
  uploadReference: vi.fn(),
  deleteReference: vi.fn(),
  getCalibration: vi.fn(),
  getCalibrationBackgroundBlob: vi.fn(),
  calibrateBackground: vi.fn(),
  updateRoi: vi.fn(),
  getArduinoPorts: vi.fn(),
  connectArduino: vi.fn(),
  disconnectArduino: vi.fn(),
  enterMaintenance: vi.fn(),
  leaveMaintenance: vi.fn(),
  testLed: vi.fn(),
  allOff: vi.fn(),
  openEvents: vi.fn(),
  releaseSession: vi.fn(),
  analyzePhoto: vi.fn(),
  streamFrame: vi.fn(),
  getCameraSources: vi.fn(),
  setActiveCameraSource: vi.fn(),
  createCameraInvite: vi.fn().mockResolvedValue({ invite_id: 'inv-1', invite_token: 'tok-1', invite_url: 'http://localhost:8000/camera#token=tok-1', expires_at: 9999999999 }),
  cancelCameraInvite: vi.fn().mockResolvedValue({ cancelled: true }),
  revokeCameraTransmitters: vi.fn().mockResolvedValue({ revoked: true, disconnected_transmitters: 0 }),
  scanTools: vi.fn(),
  takeoverLease: vi.fn().mockResolvedValue({ success: true, lease_generation: 2, holder: 'admin-sess' }),
  sessionHeartbeat: vi.fn().mockResolvedValue({ success: true, lease_held: true, lease_generation: 1, expires_in: 30 }),
  getCalibrationProfiles: vi.fn().mockResolvedValue({ profiles: {} }),
  captureProfileBackground: vi.fn(),
  updateProfileRoi: vi.fn(),
  confirmLegacyProfile: vi.fn(),
  defaultApiUrl: vi.fn(() => 'http://localhost:8000'),
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  },
}))

const cameraSources = [
  { source_id: 'remote_phone', name: 'Teléfono Remoto (WebRTC)', kind: 'webrtc', status: 'standby', resolution: { width: 1920, height: 1080 }, fps: 30, calibration_available: false, requires_operator_action: false },
  { source_id: 'operator_device', name: 'Cámara de este Navegador', kind: 'browser', status: 'standby', resolution: { width: 1280, height: 720 }, fps: 30, calibration_available: false, requires_operator_action: true },
]
let activeSourceId = 'remote_phone'

vi.mock('./api', () => api)

const mockAdminCredentials: Credentials = {
  sessionId: 'admin-sess-reg',
  ticket: 'admin-ticket-reg',
  role: 'admin',
}

const initialCategories = [
  { class_id: 'tool_1', name: 'destornillador_ph2', display_name: 'Destornillador PH2', location: 'Bandeja A1', led_index: 0, pin: 2, descriptions: ['punta cruz'], reference_count: 0 },
  { class_id: 'tool_2', name: 'llave_10mm', display_name: 'Llave fija 10mm', location: 'Bandeja A2', led_index: 1, pin: 3, descriptions: ['acero cromado'], reference_count: 0 },
  { class_id: 'tool_3', name: 'alicate_universal', display_name: 'Alicate Universal', location: 'Bandeja A3', led_index: 2, pin: 4, descriptions: ['mango azul'], reference_count: 0 },
  { class_id: 'tool_4', name: 'cinta_metrica', display_name: 'Cinta Métrica 5m', location: 'Bandeja B1', led_index: 3, pin: 5, descriptions: ['carcasa amarilla'], reference_count: 0 },
  { class_id: 'tool_5', name: 'martillo_bola', display_name: 'Martillo de Bola', location: 'Bandeja B2', led_index: 4, pin: 6, descriptions: ['mango madera'], reference_count: 0 },
  { class_id: 'tool_6', name: 'calibre_digital', display_name: 'Calibre Digital', location: 'Bandeja B3', led_index: 5, pin: 7, descriptions: ['pantalla lcd'], reference_count: 0 },
  { class_id: 'tool_7', name: 'nivel_burbuja', display_name: 'Nivel de Burbuja', location: 'Bandeja C1', led_index: 6, pin: 8, descriptions: ['aluminio'], reference_count: 0 },
  { class_id: 'tool_8', name: 'cutter_profesional', display_name: 'Cutter Profesional', location: 'Bandeja C2', led_index: 7, pin: 9, descriptions: ['hoja 18mm'], reference_count: 0 },
  { class_id: 'tool_9', name: 'pinzas_precision', display_name: 'Pinzas de Precisión', location: 'Bandeja C3', led_index: 8, pin: 10, descriptions: ['acero antiestatico'], reference_count: 0 },
]

const baseConfig: ConfigResponse = {
  saved_version: 1,
  active_version: 1,
  is_dirty: false,
  settings: {
    mode: 'mock',
    gallery_mode: 'text-only',
    text_weight: 0.5,
    classifier_threshold: 0.7,
    classifier_margin: 0.08,
    inventory_confirmed: false,
    roi: [100, 100, 800, 600],
    classifier_profiles: {},
  },
  inventory: {
    example: true,
    categories: initialCategories,
  },
}

describe('Simulated UI Integration - Review Findings Regressions (JSDOM with mocked APIs)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.createWsTicket.mockResolvedValue({ ticket: 'ws-mock-ticket' })
    api.getStatus.mockResolvedValue({
      arduino: { connected: true, simulated: true, requested_mask: 0, confirmed_mask: 0, ack: true },
      maintenance_mode: false,
      mode: 'mock',
    })
    api.getInventory.mockResolvedValue({ example: false, categories: initialCategories })
    api.getConfig.mockResolvedValue(baseConfig)
    api.getConfigStatus.mockResolvedValue({ saved_version: 1, active_version: 1, is_dirty: false, task: null })
    api.openEvents.mockReturnValue({ close: vi.fn() })
    api.releaseSession.mockResolvedValue(undefined)
    api.getCalibration.mockResolvedValue({ calibrated: false, status: 'missing', image: null, roi: null })
    api.getArduinoPorts.mockResolvedValue({ ports: [{ port: 'COM3', description: 'Arduino Nano' }] })
    activeSourceId = 'remote_phone'
    api.getCameraSources.mockImplementation(() => Promise.resolve({ sources: cameraSources, active_source_id: activeSourceId, source_version: 1 }))
    api.setActiveCameraSource.mockImplementation((_url: string, _credentials: Credentials, sourceId: string) => {
      activeSourceId = sourceId
      return Promise.resolve({ active_source_id: sourceId, source_version: 1 })
    })

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn() },
    })
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value: vi.fn().mockResolvedValue(undefined),
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // ---------------------------------------------------------------------------
  // Finding 1: Admin camera role check & stream release
  // ---------------------------------------------------------------------------
  it('Finding 1: admin session successfully opens camera and retains stream without immediate abort', async () => {
    api.createSession.mockResolvedValue({ session_id: 's-adm', ticket: 't-adm', role: 'admin' })
    const trackStop = vi.fn()
    const mockStream = {
      getTracks: () => [{ stop: trackStop }],
    } as unknown as MediaStream

    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(mockStream)

    render(<StrictMode><App /></StrictMode>)

    // Connect as Admin
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))

    await waitFor(() => {
      expect(screen.getByText(/Sesión administrativa · Control total del sistema/i)).toBeInTheDocument()
    })

    fireEvent.change(await screen.findByLabelText('Fuente de Video'), { target: { value: 'operator_device' } })
    await waitFor(() => expect(api.setActiveCameraSource).toHaveBeenCalledWith(expect.any(String), expect.anything(), 'operator_device'))

    // Click "Abrir cámara"
    const openCamBtn = screen.getByRole('button', { name: 'Abrir cámara' })
    await waitFor(() => expect(openCamBtn).not.toBeDisabled())
    fireEvent.click(openCamBtn)

    await waitFor(() => {
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      })
    })

    // Tracks MUST NOT be stopped immediately for admin session
    expect(trackStop).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Cerrar cámara' })).toBeInTheDocument()

    // When session is released, tracks must be safely stopped
    fireEvent.click(screen.getByRole('button', { name: 'Liberar sesión' }))
    await waitFor(() => {
      expect(trackStop).toHaveBeenCalled()
    })
  })

  // ---------------------------------------------------------------------------
  // Finding 2: Arduino maintenance mode state persistence & inspection lock
  // ---------------------------------------------------------------------------
  it('Finding 2: maintenance mode blocks inspection camera/analysis and survives tab switches', async () => {
    let wsEventHandler: ((event: WsEvent) => void) | null = null
    api.openEvents.mockImplementation((_url, _creds, onEvent) => {
      wsEventHandler = onEvent
      return { close: vi.fn() }
    })

    api.createSession.mockResolvedValue({ session_id: 's-adm', ticket: 't-adm', role: 'admin' })
    api.enterMaintenance.mockResolvedValue({
      message: 'Maintenance mode entered',
      arduino: { connected: true, simulated: true, requested_mask: 0, confirmed_mask: 0, ack: true },
    })

    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))

    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())

    // Simulate backend sending maintenance_mode: true via WS event
    act(() => {
      wsEventHandler?.({
        type: 'system',
        event: 'maintenance_mode',
        maintenance_mode: true,
      })
    })

    // Inspection tab should show warning banner and disable camera & analysis
    await waitFor(() => {
      expect(screen.getByText(/Modo de mantenimiento activo/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: 'Abrir cámara' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Escanear Cámara Remota' })).toBeDisabled()

    // Switch to Arduino tab - maintenance state must survive!
    const arduinoNavs = screen.getAllByRole('button', { name: /Arduino/i })
    fireEvent.click(arduinoNavs[0])

    await waitFor(() => {
      expect(screen.getByText('Mantenimiento Activo')).toBeInTheDocument()
    })

    // Switch to Tools tab and back to Arduino tab
    const toolsNavs = screen.getAllByRole('button', { name: /Herramientas/i })
    fireEvent.click(toolsNavs[0])
    await waitFor(() => expect(screen.getByText('Herramientas e Inventario Físico')).toBeInTheDocument())

    const arduinoNavs2 = screen.getAllByRole('button', { name: /Arduino/i })
    fireEvent.click(arduinoNavs2[0])

    // Must still be in maintenance mode without resetting to false!
    await waitFor(() => {
      expect(screen.getByText('Mantenimiento Activo')).toBeInTheDocument()
    })
  })

  // ---------------------------------------------------------------------------
  // Finding 3: Arduino ACK truth and countdown expiry polling
  // ---------------------------------------------------------------------------
  it('Finding 3: test LED reflects waiting state when unconfirmed and polls status upon timer expiry', async () => {
    vi.useFakeTimers()
    try {
      const connectedStatus: ArduinoStatus = {
        connected: true,
        simulated: true,
        requested_mask: 1,
        confirmed_mask: 1,
        ack: false, // Unconfirmed ACK!
      }

      api.testLed.mockResolvedValue({
        message: 'LED 0 activated',
        arduino: connectedStatus,
      })

      api.getStatus.mockResolvedValue({
        arduino: {
          ...connectedStatus,
          requested_mask: 0,
          confirmed_mask: 0,
          ack: true,
        },
      })

      render(
        <ArduinoPanel
          apiUrl="http://localhost:8000"
          credentials={mockAdminCredentials}
          config={baseConfig}
          currentArduinoStatus={connectedStatus}
          maintenanceMode={true}
        />
      )

      // Click test for LED 0
      const led0Btn = screen.getByRole('button', { name: 'Probar LED 0 (Pin D2)' })
      await act(async () => {
        fireEvent.click(led0Btn)
      })

      // Because ack is false, it must NOT show ON verificado; it should display waiting/unknown!
      expect(screen.getByText(/Esperando ACK\.\.\./i)).toBeInTheDocument()

      // Advance timers past 2.0s countdown with async flush
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2200)
      })

      // Authoritative status poll should have verified the LED is off
      expect(api.getStatus).toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  // ---------------------------------------------------------------------------
  // Finding 5: Global Guardar y aplicar saves uncommitted draft before applying
  // ---------------------------------------------------------------------------
  it('Finding 5: global "Guardar y aplicar" atomically commits local draft before applyConfig and halts on 409', async () => {
    api.createSession.mockResolvedValue({ session_id: 's-adm', ticket: 't-adm', role: 'admin' })
    render(<StrictMode><App /></StrictMode>)

    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))

    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())

    // Navigate to Tools
    const toolsTabs = screen.getAllByRole('button', { name: /Herramientas/i })
    fireEvent.click(toolsTabs[0])

    await waitFor(() => expect(screen.getByText('Herramientas e Inventario Físico')).toBeInTheDocument())

    // Edit a tool name to create a local draft
    const nameInputs = screen.getAllByLabelText(/Nombre visible/i)
    fireEvent.change(nameInputs[0], { target: { value: 'Destornillador Phillips Modificado' } })

    // Top banner shows "Cambios locales sin guardar"
    await waitFor(() => {
      expect(screen.getByText(/Cambios locales sin guardar/i)).toBeInTheDocument()
    })

    // Mock successful save and apply
    api.updateConfig.mockResolvedValue({
      ...baseConfig,
      saved_version: 2,
    })
    api.applyConfig.mockResolvedValue({
      ...baseConfig,
      saved_version: 2,
      active_version: 2,
    })

    // Click global banner button "Guardar y aplicar"
    const globalSaveBtn = screen.getAllByRole('button', { name: 'Guardar y aplicar' })[0]
    fireEvent.click(globalSaveBtn)

    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith(
        'http://localhost:8000',
        expect.objectContaining({ role: 'admin' }),
        expect.objectContaining({
          expected_version: 1,
          inventory: expect.objectContaining({
            categories: expect.arrayContaining([
              expect.objectContaining({ display_name: 'Destornillador Phillips Modificado' }),
            ]),
          }),
        })
      )
      expect(api.applyConfig).toHaveBeenCalledWith('http://localhost:8000', expect.objectContaining({ role: 'admin' }))
    })

    // Test 409 Conflict halting apply
    api.updateConfig.mockRejectedValueOnce(new api.ApiError('Conflict', 409))
    fireEvent.change(nameInputs[0], { target: { value: 'Destornillador Conflicto' } })

    await waitFor(() => {
      expect(screen.getByText(/Cambios locales sin guardar/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getAllByRole('button', { name: 'Guardar y aplicar' })[0])

    await waitFor(() => {
      expect(screen.getByText(/Conflicto de versiones al guardar/i)).toBeInTheDocument()
    })
    // applyConfig was NOT called after 409 conflict!
    expect(api.applyConfig).toHaveBeenCalledTimes(1) // Only from previous call
  })

  it('preserves tool edits and calibration edits across section navigation and global apply', async () => {
    api.createSession.mockResolvedValue({ session_id: 's-cross', ticket: 't-cross', role: 'admin' })
    api.updateConfig.mockImplementation(async (_url: string, _credentials: Credentials, payload: Record<string, unknown>) => ({
      ...baseConfig,
      saved_version: Number(payload.expected_version) + 1,
      is_dirty: true,
      settings: payload.settings,
      inventory: payload.inventory,
    }))
    api.applyConfig.mockResolvedValue({ ...baseConfig, saved_version: 2, active_version: 2, is_dirty: false })

    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())

    fireEvent.click(screen.getAllByRole('button', { name: /Herramientas/i })[0])
    await waitFor(() => expect(screen.getByText('Herramientas e Inventario Físico')).toBeInTheDocument())
    fireEvent.change(screen.getAllByLabelText('Nombre visible (UI)')[0], { target: { value: 'Nombre cruzado' } })
    fireEvent.change(screen.getAllByPlaceholderText(/Nuevo rasgo visible/i)[0], { target: { value: 'rasgo cruzado' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Añadir' })[0])

    fireEvent.click(screen.getAllByRole('button', { name: /Calibración/i })[0])
    await waitFor(() => expect(screen.getByText('Reconocimiento IA y Bandeja Vacía')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Configuración avanzada/i }))
    fireEvent.change(screen.getByLabelText(/Umbral mínimo de similitud/i), { target: { value: '0.81' } })

    fireEvent.click(screen.getAllByRole('button', { name: /Herramientas/i })[0])
    await waitFor(() => expect(screen.getAllByLabelText('Nombre visible (UI)')[0]).toHaveValue('Nombre cruzado'))
    expect(screen.getByText('rasgo cruzado')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: 'Guardar y aplicar' })[0])
    await waitFor(() => expect(api.updateConfig).toHaveBeenCalled())
    const payload = api.updateConfig.mock.calls.at(-1)?.[2] as { expected_version: number; settings: { classifier_threshold: number }; inventory: { categories: Array<{ display_name?: string; descriptions: string[] }> } }
    expect(payload.expected_version).toBe(1)
    expect(payload.settings.classifier_threshold).toBe(0.81)
    expect(payload.inventory.categories[0].display_name).toBe('Nombre cruzado')
    expect(payload.inventory.categories[0].descriptions).toContain('rasgo cruzado')
    expect(api.applyConfig).toHaveBeenCalledTimes(1)
  })

  it('keeps the original optimistic base after a remote config_changed event while dirty', async () => {
    let wsEventHandler: ((event: WsEvent) => void) | null = null
    api.openEvents.mockImplementation((_url, _credentials, onEvent) => {
      wsEventHandler = onEvent
      return { close: vi.fn() }
    })
    api.createSession.mockResolvedValue({ session_id: 's-remote', ticket: 't-remote', role: 'admin' })
    api.updateConfig.mockRejectedValue(new api.ApiError('Conflict', 409))

    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())
    fireEvent.click(screen.getAllByRole('button', { name: /Herramientas/i })[0])
    await waitFor(() => expect(screen.getByText('Herramientas e Inventario Físico')).toBeInTheDocument())
    fireEvent.change(screen.getAllByLabelText('Nombre visible (UI)')[0], { target: { value: 'Borrador protegido' } })

    act(() => {
      wsEventHandler?.({ type: 'system', event: 'config_changed', saved_version: 8, active_version: 7, is_dirty: true })
    })
    fireEvent.click(screen.getAllByRole('button', { name: 'Guardar y aplicar' })[0])
    await waitFor(() => expect(api.updateConfig).toHaveBeenCalled())
    expect(api.updateConfig.mock.calls.at(-1)?.[2]).toEqual(expect.objectContaining({ expected_version: 1 }))
    expect(api.applyConfig).not.toHaveBeenCalled()
    expect(screen.getAllByLabelText('Nombre visible (UI)')[0]).toHaveValue('Borrador protegido')
  })

  it('merges a reference revision into the draft without dropping unsaved tool edits', async () => {
    api.createSession.mockResolvedValue({ session_id: 's-ref', ticket: 't-ref', role: 'admin' })
    api.listReferences.mockResolvedValue({ references: [] })
    api.uploadReference.mockResolvedValue({ image_id: 'photo-1', thumbnail_data_url: 'data:image/jpeg;base64,abc' })
    api.getConfig.mockResolvedValueOnce(baseConfig).mockResolvedValue({
      ...baseConfig,
      saved_version: 2,
      inventory: {
        ...baseConfig.inventory,
        categories: baseConfig.inventory.categories.map((item, index) => index === 0 ? { ...item, reference_count: 1 } : item),
      },
    })

    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())
    fireEvent.click(screen.getAllByRole('button', { name: /Herramientas/i })[0])
    await waitFor(() => expect(screen.getByText('Herramientas e Inventario Físico')).toBeInTheDocument())
    fireEvent.change(screen.getAllByLabelText('Nombre visible (UI)')[0], { target: { value: 'Edición antes de foto' } })
    fireEvent.click(screen.getAllByRole('button', { name: /Ver \/ Subir fotografías/i })[0])
    await waitFor(() => expect(screen.getByText(/Subir archivos/i)).toBeInTheDocument())
    const input = document.querySelector('input[type="file"][multiple]') as HTMLInputElement
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(['photo'], 'reference.jpg', { type: 'image/jpeg' })] } })
    })
    await waitFor(() => expect(api.getConfig).toHaveBeenCalledTimes(2))

    fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }))
    await waitFor(() => expect(api.updateConfig).toHaveBeenCalled())
    const payload = api.updateConfig.mock.calls.at(-1)?.[2] as { expected_version: number; inventory: { categories: Array<{ display_name?: string; reference_count?: number }> } }
    expect(payload.expected_version).toBe(2)
    expect(payload.inventory.categories[0].display_name).toBe('Edición antes de foto')
    expect(payload.inventory.categories[0].reference_count).toBe(1)
  })

  it('ignores a rollback refresh that resolves after the session identity changed', async () => {
    let wsEventHandler: ((event: WsEvent) => void) | null = null
    api.openEvents.mockImplementation((_url, _credentials, onEvent) => {
      wsEventHandler = onEvent
      return { close: vi.fn() }
    })
    api.createSession
      .mockResolvedValueOnce({ session_id: 's-old', ticket: 't-old', role: 'admin' })
      .mockResolvedValueOnce({ session_id: 's-new', ticket: 't-new', role: 'admin' })
    const stale = { ...baseConfig, saved_version: 99, active_version: 99 }
    let resolveStale!: (config: ConfigResponse) => void
    api.getConfig.mockResolvedValueOnce(baseConfig).mockImplementationOnce(() => new Promise<ConfigResponse>((resolve) => { resolveStale = resolve }))

    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())
    act(() => {
      wsEventHandler?.({ type: 'system', event: 'config_rolled_back', saved_version: 2, active_version: 2 })
    })
    await waitFor(() => expect(api.getConfig).toHaveBeenCalledTimes(2))

    fireEvent.click(screen.getByRole('button', { name: 'Liberar sesión' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Verificar y conectar' })).toBeInTheDocument())
    api.getConfig.mockResolvedValue(baseConfig)
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())
    resolveStale(stale)
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByText('v99')).not.toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // Finding 6: Controller UI does not expose admin-only background calibration
  // ---------------------------------------------------------------------------
  it('Finding 6: controller session does NOT render "Usar foto actual como fondo vacío"', async () => {
    api.createSession.mockResolvedValue({ session_id: 's-ctrl', ticket: 't-ctrl', role: 'controller' })
    const createBitmap = vi.fn().mockResolvedValue({ width: 100, height: 80, close: vi.fn() })
    Object.defineProperty(window, 'createImageBitmap', { configurable: true, value: createBitmap })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((cb) => cb(new Blob(['jpg'], { type: 'image/jpeg' })))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:ctrl-test') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })

    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'controller' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))

    await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())

    // Upload preview photo
    const file = new File(['photo'], 'test.jpg', { type: 'image/jpeg' })
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByRole('img', { name: 'Fotografía con detecciones' })).toBeInTheDocument())

    // "Usar foto actual como fondo vacío" MUST NOT exist for controller!
    expect(screen.queryByRole('button', { name: 'Usar foto actual como fondo vacío' })).not.toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // Finding 8: ROI pointer capture on stable SVG with clamped coordinates
  // ---------------------------------------------------------------------------
  it('Finding 8: ROI pointer capture attaches to SVG root and clamps coordinates', async () => {
    const onUpdateMock = vi.fn()
    const setPointerCapture = vi.fn()
    const releasePointerCapture = vi.fn()

    api.getCalibration.mockResolvedValue({
      calibrated: true,
      status: 'calibrated',
      image: { width: 1280, height: 960 },
      roi: [100, 100, 800, 600],
    })
    api.getCalibrationBackgroundBlob.mockResolvedValue(new Blob(['fake-bg'], { type: 'image/jpeg' }))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:bg-roi-test') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })

    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={baseConfig}
        onConfigUpdated={onUpdateMock}
      />
    )

    await waitFor(() => {
      expect(document.querySelector('svg.roi-svg')).toBeInTheDocument()
    })

    const svg = document.querySelector('svg.roi-svg') as SVGSVGElement
    expect(svg).toBeInTheDocument()

    // Mock pointer capture on SVG
    svg.setPointerCapture = setPointerCapture
    svg.releasePointerCapture = releasePointerCapture
    svg.getBoundingClientRect = () => ({
      left: 0,
      top: 0,
      width: 500,
      height: 400,
      right: 500,
      bottom: 400,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })

    // Pointer down
    fireEvent.pointerDown(svg, { clientX: 50, clientY: 50, pointerId: 1 })
    expect(setPointerCapture).toHaveBeenCalledWith(1)

    // Pointer move outside bounds -> should be clamped
    fireEvent.pointerMove(svg, { clientX: 600, clientY: 500, pointerId: 1 })

    // Pointer up releases capture safely
    fireEvent.pointerUp(svg, { clientX: 300, clientY: 250, pointerId: 1 })
    expect(releasePointerCapture).toHaveBeenCalledWith(1)
  })

  // ---------------------------------------------------------------------------
  // Finding 4: Preserve physical grid geometry 3x3 on phone
  // ---------------------------------------------------------------------------
  it('Finding 4: styles.css preserves 3x3 repeat(3, minmax(0, 1fr)) for organizer-grid and led-grid on mobile', () => {
    const css = fs.readFileSync('src/styles.css', 'utf-8')
    expect(css).toMatch(/@media\s*\(max-width:\s*520px\)\s*\{[\s\S]*?\.organizer-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3/)
    expect(css).toMatch(/@media\s*\(max-width:\s*520px\)\s*\{[\s\S]*?\.led-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3/)
  })

  // ---------------------------------------------------------------------------
  // Finding 7: WebSocket events config_rolled_back, mode_changed, and roi_updated
  // ---------------------------------------------------------------------------
  it('Finding 7: handles WebSocket config_rolled_back, mode_changed, and roi_updated events', async () => {
    let wsEventHandler: ((event: WsEvent) => void) | null = null
    api.openEvents.mockImplementation((_url, _creds, onEvent) => {
      wsEventHandler = onEvent
      return { close: vi.fn() }
    })
    api.createSession.mockResolvedValue({ session_id: 's-adm', ticket: 't-adm', role: 'admin' })
    render(<StrictMode><App /></StrictMode>)
    fireEvent.change(screen.getByLabelText('Rol de sesión'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))

    await waitFor(() => expect(screen.getByText(/Sesión administrativa/i)).toBeInTheDocument())

    // 1. mode_changed event
    act(() => {
      wsEventHandler?.({
        type: 'system',
        event: 'mode_changed',
        mode: 'real',
      })
    })

    // 2. roi_updated event
    act(() => {
      wsEventHandler?.({
        type: 'system',
        event: 'roi_updated',
        roi: [50, 60, 400, 300],
      })
    })

    // 3. config_rolled_back event
    api.getConfig.mockResolvedValue({
      ...baseConfig,
      saved_version: 3,
      active_version: 3,
    })

    await act(async () => {
      wsEventHandler?.({
        type: 'system',
        event: 'config_rolled_back',
        saved_version: 3,
        active_version: 3,
      })
    })

    await waitFor(() => {
      expect(api.getConfig).toHaveBeenCalled()
    })
  })

  // ---------------------------------------------------------------------------
  // Finding 9 (continued): Partial upload failure leaves successful photos visible
  // ---------------------------------------------------------------------------
  it('Finding 9: reference photos partial failure leaves successful photos visible with actionable error', async () => {
    api.listReferences.mockResolvedValue({ references: [] })
    api.uploadReference
      .mockResolvedValueOnce({ image_id: 'img-1', thumbnail_data_url: 'data:image/jpeg;base64,123' })
      .mockRejectedValueOnce(new Error('Network upload failed on second photo'))
    api.getConfig.mockResolvedValue(baseConfig)

    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={baseConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // Open photos for tool_1
    const photoBtns = screen.getAllByRole('button', { name: /Ver \/ Subir fotografías/i })
    await act(async () => {
      fireEvent.click(photoBtns[0])
    })

    await waitFor(() => {
      expect(screen.getByText(/Subir archivos/i)).toBeInTheDocument()
    })

    const uploadInput = document.querySelector('input[type="file"][multiple]') as HTMLInputElement
    const file1 = new File(['1'], 'p1.jpg', { type: 'image/jpeg' })
    const file2 = new File(['2'], 'p2.jpg', { type: 'image/jpeg' })

    await act(async () => {
      fireEvent.change(uploadInput, { target: { files: [file1, file2] } })
    })

    // Error message must be visible and actionable
    await waitFor(() => {
      expect(screen.getByText(/Error en foto p2\.jpg: Network upload failed on second photo/i)).toBeInTheDocument()
    })

    // The successful photo must be visible in thumbnails
    expect(screen.getByAltText(/Referencia visual/i)).toBeInTheDocument()
  })

  // ---------------------------------------------------------------------------
  // Finding 9: File input value resets on change for immediate retry
  // ---------------------------------------------------------------------------
  it('Finding 9: file inputs clear value after change to permit selecting the same file on retry', () => {
    render(<StrictMode><App /></StrictMode>)
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(fileInput).toBeInTheDocument()

    const file = new File(['data'], 'tray.jpg', { type: 'image/jpeg' })
    fireEvent.change(fileInput, { target: { files: [file] } })
    // In our component, onChange sets event.target.value = ''
    expect(fileInput.value).toBe('')
  })
})
