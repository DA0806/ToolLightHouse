import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { ConfigResponse, SystemReadiness } from './types'

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
    status = 0
  },
}))

const cameraSources = [
  { source_id: 'operator_device', name: 'Cámara de este Navegador', kind: 'browser', status: 'standby', resolution: { width: 1280, height: 720 }, fps: 30, calibration_available: false, requires_operator_action: true },
  { source_id: 'manual_upload', name: 'Carga Manual de Fotografía', kind: 'file', status: 'standby', resolution: { width: 0, height: 0 }, fps: 0, calibration_available: false, requires_operator_action: true },
]

vi.mock('./api', () => api)

const baseCategories = Array.from({ length: 9 }, (_, idx) => ({
  class_id: `tool_${idx + 1}`,
  name: `tool_${idx + 1}`,
  display_name: `Herramienta ${idx + 1}`,
  descriptions: [`Rasgo visual distintivo ${idx + 1}`],
  location: `Casillero ${idx + 1}`,
  led_index: idx,
  pin: idx + 2,
  reference_count: 0,
}))

let currentConfig: ConfigResponse = {
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
    categories: baseCategories,
  },
}

const mockReadiness: SystemReadiness = {
  backend_ok: true,
  python_version: '3.11.9',
  torch_available: true,
  cuda_available: true,
  gpu_name: 'NVIDIA RTX 4070',
  vram_free_mb: 8192,
  model_present: false,
  model_status: 'missing_weights',
  inventory_ready: false,
  gallery_ready: false,
  calibration_ready: false,
  arduino_ready: false,
  active_mode: 'mock',
  can_activate_real: false,
  preparation_reasons: ['Inventario sin confirmar'],
}

describe('Simulated UI Integration - Dashboard User Lifecycle (JSDOM with mocked APIs)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    currentConfig = {
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
        categories: [...baseCategories],
      },
    }

    api.createSession.mockResolvedValue({ session_id: 'admin-sess-e2e', ticket: 't-e2e', role: 'admin' })
    api.getCameraSources.mockResolvedValue({ sources: cameraSources, active_source_id: 'operator_device', source_version: 1 })
    api.setActiveCameraSource.mockResolvedValue({ active_source_id: 'operator_device', source_version: 1 })
    api.createWsTicket.mockResolvedValue({ ticket: 'ws-e2e' })
    api.getStatus.mockResolvedValue({ arduino: { connected: false, simulated: false } })
    api.getInventory.mockResolvedValue({ example: true, categories: baseCategories })
    api.getConfig.mockImplementation(() => Promise.resolve(currentConfig))
    api.getConfigStatus.mockResolvedValue({ saved_version: 1, active_version: 1, is_dirty: false, task: null })
    api.openEvents.mockReturnValue({ close: vi.fn() })
    api.releaseSession.mockResolvedValue(undefined)
    api.getReadiness.mockResolvedValue(mockReadiness)
    api.getCalibration.mockResolvedValue({ calibrated: false, status: 'missing', image: null, roi: null })
    api.getArduinoPorts.mockResolvedValue({ ports: [{ port: 'COM3', description: 'Arduino Nano CH340' }] })
    api.updateConfig.mockImplementation((_url, _creds, payload) => {
      currentConfig = {
        ...currentConfig,
        saved_version: currentConfig.saved_version + 1,
        is_dirty: true,
        settings: payload.settings,
        inventory: payload.inventory,
      }
      return Promise.resolve(currentConfig)
    })
    api.applyConfig.mockImplementation(() => {
      currentConfig = {
        ...currentConfig,
        active_version: currentConfig.saved_version,
        is_dirty: false,
      }
      return Promise.resolve(currentConfig)
    })
  })

  it('completes the full mobile admin workflow across all 5 sections', async () => {
    render(
      <StrictMode>
        <App />
      </StrictMode>
    )

    // -------------------------------------------------------------------------
    // Phase 1: Admin Pairing
    // -------------------------------------------------------------------------
    // Select Admin role
    const roleSelect = screen.getByLabelText('Rol de sesión')
    fireEvent.change(roleSelect, { target: { value: 'admin' } })
    expect(screen.getByLabelText('Clave de administrador')).toBeInTheDocument()

    // Enter admin key
    fireEvent.change(screen.getByLabelText('Clave de administrador'), { target: { value: 'adminkey123' } })

    // Click connect
    fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))

    await waitFor(() => {
      expect(screen.getByText('Sesión administrativa · Control total del sistema')).toBeInTheDocument()
    })
    expect(api.createSession).toHaveBeenCalledWith(expect.any(String), 'adminkey123', expect.any(String), 'admin')

    // -------------------------------------------------------------------------
    // Phase 2: System Section & Prepare AI
    // -------------------------------------------------------------------------
    // Switch to "Sistema" tab via navigation
    const systemTabs = screen.getAllByRole('button', { name: /Sistema/i })
    fireEvent.click(systemTabs[0])

    await waitFor(() => {
      expect(screen.getByText('Asistente de Configuración Inicial (7 Puntos)')).toBeInTheDocument()
    })
    expect(screen.getByText(/NVIDIA RTX 4070/i)).toBeInTheDocument()

    // Mock prepare AI endpoints
    api.setSystemMode.mockResolvedValue({
      mode: 'preparation',
      readiness: { ...mockReadiness, active_mode: 'preparation' },
    })
    const prepBtn = screen.getByRole('button', { name: 'Preparar IA' })
    fireEvent.click(prepBtn)

    await waitFor(() => {
      expect(api.setSystemMode).toHaveBeenCalledWith(expect.any(String), expect.anything(), 'preparation')
      expect(api.applyConfig).toHaveBeenCalled()
    })

    // -------------------------------------------------------------------------
    // Phase 3: Herramientas & Physical Inventory Configuration
    // -------------------------------------------------------------------------
    const toolsTabs = screen.getAllByRole('button', { name: /Herramientas/i })
    fireEvent.click(toolsTabs[0])

    await waitFor(() => {
      expect(screen.getByText('Herramientas e Inventario Físico')).toBeInTheDocument()
    })

    // 9 tool cards & 3x3 organizer grid
    expect(screen.getAllByRole('button', { name: /LED \d, Pin D\d+/i })).toHaveLength(9)

    // Edit display name of tool_1
    const displayInputs = screen.getAllByPlaceholderText(/Ej\. Martillo de uña/i)
    fireEvent.change(displayInputs[0], { target: { value: 'Martillo de Uña Curva' } })

    // Add description to tool_1
    const descInputs = screen.getAllByPlaceholderText(/Nuevo rasgo visible/i)
    fireEvent.change(descInputs[0], { target: { value: 'Cabeza forjada con muesca sacaclavos pulida' } })
    const addDescBtns = screen.getAllByRole('button', { name: 'Añadir' })
    fireEvent.click(addDescBtns[0])

    expect(screen.getByText('Cabeza forjada con muesca sacaclavos pulida')).toBeInTheDocument()

    // Atomic LED Swap: Change tool_1 LED from 0 to 1
    const ledSelects = screen.getAllByLabelText('Selector de LED')
    fireEvent.change(ledSelects[0], { target: { value: '1' } })

    // tool_1 is now 1, tool_2 is now 0 (swapped atomically)
    expect((ledSelects[0] as HTMLSelectElement).value).toBe('1')
    expect((ledSelects[1] as HTMLSelectElement).value).toBe('0')

    // Confirm physical inventory
    const confirmInvBtn = screen.getByRole('button', { name: 'Confirmar inventario real' })
    fireEvent.click(confirmInvBtn)
    expect(screen.getByText('Inventario real confirmado')).toBeInTheDocument()

    // Save and apply changes
    const saveApplyBtns = screen.getAllByRole('button', { name: 'Guardar y aplicar' })
    fireEvent.click(saveApplyBtns[saveApplyBtns.length - 1])

    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalled()
      expect(api.applyConfig).toHaveBeenCalled()
      expect(screen.getByText(/Configuración aplicada en caliente con éxito/i)).toBeInTheDocument()
    })

    // -------------------------------------------------------------------------
    // Phase 4: Calibración & ROI
    // -------------------------------------------------------------------------
    const calibTabs = screen.getAllByRole('button', { name: /Calibración/i })
    fireEvent.click(calibTabs[0])

    await waitFor(() => {
      expect(screen.getByText('Reconocimiento IA y Bandeja Vacía')).toBeInTheDocument()
    })

    // Toggle Hybrid mode ("Mejorar con fotografías")
    const hybridSwitch = screen.getByRole('checkbox', { name: 'Interruptor de modo híbrido' })
    fireEvent.click(hybridSwitch)

    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith(
        expect.any(String),
        expect.anything(),
        expect.objectContaining({
          settings: expect.objectContaining({ gallery_mode: 'hybrid' }),
        })
      )
    })

    // Mock update ROI
    api.updateRoi.mockResolvedValue({ roi: [50, 50, 900, 700] })
    const roiSaveBtn = screen.getByRole('button', { name: 'Guardar ROI' })
    fireEvent.click(roiSaveBtn)

    await waitFor(() => {
      expect(api.updateRoi).toHaveBeenCalledWith(expect.any(String), expect.anything(), [100, 100, 800, 600])
    })

    // -------------------------------------------------------------------------
    // Phase 5: Arduino Diagnostics & Unit LED Test
    // -------------------------------------------------------------------------
    const arduinoTabs = screen.getAllByRole('button', { name: /Arduino/i })
    fireEvent.click(arduinoTabs[0])

    await waitFor(() => {
      expect(screen.getByText('Diagnóstico y Control de Arduino Nano')).toBeInTheDocument()
    })

    // Connect simulated Arduino
    api.connectArduino.mockResolvedValue({
      message: 'Simulated connection active',
      arduino: { connected: true, simulated: true, requested_mask: 0, confirmed_mask: 0, ack: true },
    })

    const simCheck = screen.getByRole('checkbox', { name: /Usar conexión serial simulada/i })
    fireEvent.click(simCheck)
    fireEvent.click(screen.getByRole('button', { name: 'Conectar Arduino' }))

    await waitFor(() => {
      expect(screen.getByText('Simulación en software')).toBeInTheDocument()
    })

    // Enter maintenance mode to enable LED test
    api.enterMaintenance.mockResolvedValue({
      maintenance_mode: true,
      arduino: { connected: true, simulated: true, requested_mask: 0, confirmed_mask: 0, ack: true },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar a Modo Mantenimiento' }))

    await waitFor(() => {
      expect(screen.getByText('Mantenimiento Activo')).toBeInTheDocument()
    })

    // Test LED 0 (duration 2.0s)
    api.testLed.mockResolvedValue({
      message: 'LED 0 activated for 2.0s',
      led_index: 0,
      pin: 2,
      duration_seconds: 2.0,
      arduino: { connected: true, simulated: true, requested_mask: 1, confirmed_mask: 1, ack: true },
    })

    const led0TestBtn = screen.getByRole('button', { name: 'Probar LED 0 (Pin D2)' })
    fireEvent.click(led0TestBtn)

    await waitFor(() => {
      expect(api.testLed).toHaveBeenCalledWith(expect.any(String), expect.anything(), 0, 2.0)
    })

    // Exit maintenance mode
    api.leaveMaintenance.mockResolvedValue({
      maintenance_mode: false,
      arduino: { connected: true, simulated: true, requested_mask: 0, confirmed_mask: 0, ack: true },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Salir de Modo Mantenimiento' }))

    await waitFor(() => {
      expect(screen.getByText('Operación Normal')).toBeInTheDocument()
    })

    // -------------------------------------------------------------------------
    // Phase 6: Return to Inspection & Verify Mock Detection
    // -------------------------------------------------------------------------
    const backBtn = screen.getByRole('button', { name: /Volver a Inspección/i })
    fireEvent.click(backBtn)

    await waitFor(() => {
      expect(screen.getByText('Inspección fotográfica')).toBeInTheDocument()
    })

    // Simulate choosing and inspecting a photo
    const createBitmap = vi.fn().mockResolvedValue({ width: 100, height: 80, close: vi.fn() })
    Object.defineProperty(window, 'createImageBitmap', { configurable: true, value: createBitmap })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((cb) => cb(new Blob(['test'], { type: 'image/jpeg' })))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:e2e-photo') })

    api.analyzePhoto.mockResolvedValue({
      frame_id: 'frame-e2e',
      timestamp: 1,
      mode: 'mock',
      simulated: true,
      image: { width: 100, height: 80, orientation: 'normalized' },
      detections: [
        { track_id: 1, bbox: [15, 25, 40, 30], class_id: 'tool_1', name: 'Martillo de Uña Curva', validation: 'simulated' },
      ],
      arduino: { requested_mask: 1, confirmed_mask: 1, connected: true, simulated: true, ack: true },
    })

    const file = new File(['photo'], 'inspect.jpg', { type: 'image/jpeg' })
    const fileInput = document.querySelector('input[type="file"]')!
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'Fotografía con detecciones' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Analizar foto' }))

    await waitFor(() => {
      expect(screen.getAllByText('Martillo de Uña Curva').length).toBeGreaterThanOrEqual(1)
    })

    // Verify SVG detection box
    const rect = document.querySelector('rect.bbox')
    expect(rect?.getAttribute('x')).toBe('15')
    expect(rect?.getAttribute('y')).toBe('25')
    expect(screen.getByText('ACK simulado')).toBeInTheDocument()
    expect(screen.getByText('1 detectada / 8 no detectadas')).toBeInTheDocument()
    expect(screen.getAllByText('NO DETECTADA EN ESTA LECTURA')).toHaveLength(8)
  }, 10000)
})
