import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App, { isTrueAuthorityLoss, mapActionableErrorMessage } from './App'
import { parseErrorBody } from './api'

const api = vi.hoisted(() => ({
  createSession: vi.fn(),
  createWsTicket: vi.fn(),
  getConfig: vi.fn().mockResolvedValue({
    saved_version: 1,
    active_version: 1,
    is_dirty: false,
    settings: { mode: 'mock', calibration_profiles: {} },
    inventory: { categories: [] },
  }),
  getConfigStatus: vi.fn().mockResolvedValue({ saved_version: 1, active_version: 1, is_dirty: false }),
  getStatus: vi.fn(),
  getInventory: vi.fn(),
  openEvents: vi.fn(),
  releaseSession: vi.fn(),
  analyzePhoto: vi.fn(),
  streamFrame: vi.fn(),
  stopStream: vi.fn().mockResolvedValue({ stopped: true, stream_generation: 1 }),
  calibrateBackground: vi.fn(),
  defaultApiUrl: vi.fn(() => 'http://localhost:8000'),
  getCameraSources: vi.fn(),
  setActiveCameraSource: vi.fn(),
  createCameraInvite: vi.fn().mockResolvedValue({ invite_id: 'inv-1', invite_token: 'tok-1', invite_url: 'http://localhost:8000/camera#token=tok-1', expires_at: 9999999999 }),
  cancelCameraInvite: vi.fn().mockResolvedValue({ cancelled: true }),
  revokeCameraTransmitters: vi.fn().mockResolvedValue({ revoked: true, disconnected_transmitters: 0 }),
  scanTools: vi.fn(),
  takeoverLease: vi.fn().mockResolvedValue({ success: true, lease_generation: 2, holder: 'admin-sess' }),
  sessionHeartbeat: vi.fn(),
  getCalibrationProfiles: vi.fn().mockResolvedValue({ profiles: {} }),
  captureProfileBackground: vi.fn(),
  updateProfileRoi: vi.fn(),
  confirmLegacyProfile: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    code?: string
    constructor(message: string, status = 0, code?: string) {
      super(message)
      this.status = status
      this.code = code
    }
  },
}))
vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>()
  return {
    ...actual,
    ...api,
  }
})

describe('Audit Corrections Regression Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getConfig.mockResolvedValue({
      saved_version: 1,
      active_version: 1,
      is_dirty: false,
      settings: { mode: 'mock', calibration_profiles: {} },
      inventory: { categories: [] },
    })
    api.getConfigStatus.mockResolvedValue({ saved_version: 1, active_version: 1, is_dirty: false })
    api.createSession.mockResolvedValue({ session_id: 's-audit-1', ticket: 't-audit-1', role: 'controller' })
    api.createWsTicket.mockResolvedValue({ ticket: 'ws-audit-1' })
    api.getStatus.mockResolvedValue({ arduino: { simulated: true } })
    api.getInventory.mockResolvedValue({ categories: [] })
    api.openEvents.mockReturnValue({ close: vi.fn() })
    api.releaseSession.mockResolvedValue(undefined)
    api.sessionHeartbeat.mockResolvedValue({ success: true, alive: true, lease_held: true, lease_generation: 1, expires_in: 30 })
    api.getCameraSources.mockResolvedValue({
      sources: [
        {
          source_id: 'laptop_builtin',
          name: 'Cámara Integrada',
          kind: 'opencv',
          status: 'active',
          resolution: { width: 1280, height: 720 },
          fps: 30,
          requires_operator_action: false,
          calibration_available: true,
        },
        {
          source_id: 'operator_device',
          name: 'Cámara de este Navegador',
          kind: 'browser',
          status: 'standby',
          resolution: { width: 1280, height: 720 },
          fps: 30,
          requires_operator_action: true,
          calibration_available: false,
        },
      ],
      active_source_id: 'laptop_builtin',
      source_version: 1,
    })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn() } })
  })

  describe('Structured Error & Backend Code Parsing', () => {
    it('parseErrorBody parses string detail correctly', () => {
      const parsed = { detail: 'Simple string error' }
      const res = parseErrorBody(parsed, 400)
      expect(res.message).toBe('Simple string error')
      expect(res.code).toBeUndefined()
    })

    it('parseErrorBody parses structured object detail without [object Object]', () => {
      const parsed = {
        detail: {
          code: 'source_stale',
          message: 'Source version is out of sync',
        },
      }
      const res = parseErrorBody(parsed, 409)
      expect(res.message).toBe('Source version is out of sync')
      expect(res.code).toBe('source_stale')
      expect(res.message).not.toContain('[object Object]')
    })

    it('parseErrorBody falls back gracefully to JSON string for complex detail', () => {
      const parsed = {
        detail: {
          foo: 'bar',
          baz: 123,
        },
      }
      const res = parseErrorBody(parsed, 422)
      expect(res.message).toContain('"foo":"bar"')
      expect(res.message).not.toContain('[object Object]')
    })
  })

  describe('Error Mapping & Authority Loss Distinction', () => {
    const errorTestCases = [
      {
        name: 'source changed / version stale',
        error: new api.ApiError('stream source version is stale', 409),
        expectedMessage: /La fuente de cámara cambió o está desactualizada/i,
        isAuthorityLoss: false,
      },
      {
        name: 'calibration missing / incompatible resolution',
        error: new api.ApiError('calibration profile is incompatible with the active source resolution', 409),
        expectedMessage: /La calibración de la bandeja no coincide con la resolución o está en progreso/i,
        isAuthorityLoss: false,
      },
      {
        name: 'preparation mode / gallery missing',
        error: new api.ApiError('system is in preparation mode: active gallery unavailable or stale', 409),
        expectedMessage: /El sistema está en modo preparación/i,
        isAuthorityLoss: false,
      },
      {
        name: 'hardware in maintenance mode',
        error: new api.ApiError('hardware is in maintenance mode', 409),
        expectedMessage: /El hardware se encuentra en modo mantenimiento/i,
        isAuthorityLoss: false,
      },
      {
        name: 'discarded / stale webcam frame',
        error: new api.ApiError('Captured frame from webcam is stale', 409),
        expectedMessage: /El fotograma capturado fue descartado por retraso/i,
        isAuthorityLoss: false,
      },
      {
        name: 'configuration preparation task running',
        error: new api.ApiError('A configuration preparation or apply task is currently running; retry after completion', 409),
        expectedMessage: /Hay una tarea de configuración en ejecución/i,
        isAuthorityLoss: false,
      },
      {
        name: 'true authority loss 409 (controller lease expired)',
        error: new api.ApiError('controller lease expired', 409),
        expectedMessage: /El control físico fue tomado por otro operador o expiró/i,
        isAuthorityLoss: true,
      },
      {
        name: 'true authority loss 409 (another controller session is active)',
        error: new api.ApiError('another controller session is active', 409),
        expectedMessage: /El control físico fue tomado por otro operador o expiró/i,
        isAuthorityLoss: true,
      },
      {
        name: 'unknown 409 preserves backend detail verbatim',
        error: new api.ApiError('Unique backend domain conflict message', 409),
        expectedMessage: /Unique backend domain conflict message/i,
        isAuthorityLoss: false,
      },
      {
        name: '403 forbidden requires admin role',
        error: new api.ApiError('admin role required', 403),
        expectedMessage: /Permisos insuficientes para realizar esta operación/i,
        isAuthorityLoss: false,
      },
    ]

    for (const tc of errorTestCases) {
      it(`maps ${tc.name} properly and checks authority loss = ${tc.isAuthorityLoss}`, () => {
        expect(isTrueAuthorityLoss(tc.error.status, tc.error.message, tc.error.code)).toBe(tc.isAuthorityLoss)
        const mapped = mapActionableErrorMessage(tc.error)
        expect(mapped).toMatch(tc.expectedMessage)
      })
    }

    it('does not demote lease on unrelated Spanish string mentioning control físico', () => {
      // Loose matching on 'control físico' previously caused false demotion
      const unrelatedMsg = 'Advertencia sobre el entorno de control físico del robot'
      expect(isTrueAuthorityLoss(409, unrelatedMsg)).toBe(false)
    })
  })

  describe('Component Error Behavior & Session Management', () => {
    it('401 error in inspect invalidates session and returns to pairing screen', async () => {
      api.scanTools.mockRejectedValue(new api.ApiError('session expired or invalid', 401))
      api.analyzePhoto.mockRejectedValue(new api.ApiError('session expired or invalid', 401))
      render(<StrictMode><App /></StrictMode>)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())

      const scanBtn = await screen.findByRole('button', { name: /(Escanear Cámara|Analizar foto)/i })
      fireEvent.click(scanBtn)

      await waitFor(() => {
        // Session invalidated -> returned to pairing screen
        expect(screen.getByRole('button', { name: 'Verificar y conectar' })).toBeInTheDocument()
        expect((screen.getByLabelText('ID de este teléfono') as HTMLInputElement).disabled).toBe(false)
        expect(screen.getAllByText(/La sesión expiró o es inválida/i).length).toBeGreaterThan(0)
      })
    })

    it('409 true authority loss demotes lease message but does NOT wipe session credentials', async () => {
      api.scanTools.mockRejectedValue(new api.ApiError('controller lease expired', 409))
      api.analyzePhoto.mockRejectedValue(new api.ApiError('controller lease expired', 409))
      render(<StrictMode><App /></StrictMode>)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())

      const scanBtn = await screen.findByRole('button', { name: /(Escanear Cámara|Analizar foto)/i })
      fireEvent.click(scanBtn)

      await waitFor(() => {
        // Lease message shown, but session credentials preserved
        expect(screen.getAllByText(/El control físico fue tomado por otro operador o expiró/i).length).toBeGreaterThan(0)
        expect(screen.getByRole('button', { name: 'Liberar sesión' })).toBeInTheDocument()
        expect((screen.getByLabelText('ID de este teléfono') as HTMLInputElement).disabled).toBe(true)
      })
    })

    it('409 unrelated error (maintenance mode) displays Spanish message and does NOT demote lease or wipe session', async () => {
      api.scanTools.mockRejectedValue(new api.ApiError('hardware is in maintenance mode', 409))
      api.analyzePhoto.mockRejectedValue(new api.ApiError('hardware is in maintenance mode', 409))
      render(<StrictMode><App /></StrictMode>)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())

      const scanBtn = await screen.findByRole('button', { name: /(Escanear Cámara|Analizar foto)/i })
      fireEvent.click(scanBtn)

      await waitFor(() => {
        // Actionable Spanish error displayed
        expect(screen.getByText(/El hardware se encuentra en modo mantenimiento/i)).toBeInTheDocument()
        // Session remains active and lease remains held
        expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Liberar sesión' })).toBeInTheDocument()
        expect((screen.getByLabelText('ID de este teléfono') as HTMLInputElement).disabled).toBe(true)
      })
    })

    it('403 forbidden displays Spanish permissions message and does NOT wipe session', async () => {
      api.scanTools.mockRejectedValue(new api.ApiError('admin role required', 403))
      api.analyzePhoto.mockRejectedValue(new api.ApiError('admin role required', 403))
      render(<StrictMode><App /></StrictMode>)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await waitFor(() => expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument())

      const scanBtn = await screen.findByRole('button', { name: /(Escanear Cámara|Analizar foto)/i })
      fireEvent.click(scanBtn)

      await waitFor(() => {
        expect(screen.getByText(/Permisos insuficientes para realizar esta operación/i)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Liberar sesión' })).toBeInTheDocument()
      })
    })

    it('heartbeat reporting lease_held=false preserves auth and updates lease message without wiping credentials', async () => {
      vi.useFakeTimers()
      api.sessionHeartbeat.mockResolvedValue({
        success: true,
        alive: true,
        lease_held: false,
        lease_generation: 2,
        expires_in: 30,
      })
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100)
      })

      // Advance 10s for heartbeat interval
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10500)
      })

      // Credentials must NOT be wiped: input remains disabled, Liberar sesión still present
      expect((screen.getByLabelText('ID de este teléfono') as HTMLInputElement).disabled).toBe(true)
      expect(screen.getByRole('button', { name: 'Liberar sesión' })).toBeInTheDocument()
      expect(screen.getByText(/El control físico fue tomado por otro operador o expiró/i)).toBeInTheDocument()
      vi.useRealTimers()
    })

    it('continuous mode recovers from transient 409 error without stopping or wiping session', async () => {
      vi.useFakeTimers()
      // First call fails with transient stale frame
      api.streamFrame.mockRejectedValueOnce(new api.ApiError('Captured frame from webcam is stale', 409))
      // Second call succeeds
      api.streamFrame.mockResolvedValueOnce({
        accepted: true,
        frame_id: 'frame-rec-1',
        latest_only: true,
        result: {
          frame_id: 'frame-rec-1',
          timestamp: 1,
          mode: 'mock',
          simulated: true,
          image: { width: 1280, height: 720, orientation: 'normalized' },
          detections: [],
          arduino: { requested_mask: 0, confirmed_mask: 0, connected: true, simulated: true, ack: true },
        },
      })

      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })

      const checkbox = screen.getByRole('checkbox', { name: /Continuo/i })
      fireEvent.click(checkbox)

      // First tick triggers the failing call
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })

      // Error is displayed in Spanish, but continuous mode remains enabled!
      expect(screen.getByText(/El fotograma capturado fue descartado por retraso/i)).toBeInTheDocument()
      expect((checkbox as HTMLInputElement).checked).toBe(true)
      expect(screen.getByText('Control exclusivo de este teléfono')).toBeInTheDocument()

      // Second tick recovers and succeeds
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })

      expect(api.streamFrame).toHaveBeenCalledTimes(2)
      expect((checkbox as HTMLInputElement).checked).toBe(true)
      vi.useRealTimers()
    })

    it('stopping continuous mode invokes stopStream cancel fence', async () => {
      vi.useFakeTimers()
      api.streamFrame.mockResolvedValue({
        accepted: true,
        frame_id: 'f-1',
        latest_only: true,
      })

      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })

      const checkbox = screen.getByRole('checkbox', { name: /Continuo/i })
      fireEvent.click(checkbox) // turn on
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })

      fireEvent.click(checkbox) // turn off
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100)
      })

      expect(api.stopStream).toHaveBeenCalled()
      vi.useRealTimers()
    })

    it('does not scan after a failed continuous stop and preserves the session', async () => {
      vi.useFakeTimers()
      api.streamFrame.mockResolvedValue({ accepted: true, frame_id: 'f-stop', latest_only: true })
      api.stopStream.mockRejectedValueOnce(new api.ApiError('network stop failed', 503))

      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Verificar y conectar' }))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })

      const checkbox = screen.getByRole('checkbox', { name: /Continuo/i })
      fireEvent.click(checkbox)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(600)
      })
      fireEvent.click(checkbox)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100)
      })

      fireEvent.click(screen.getByRole('button', { name: 'Escanear Cámara Web' }))
      await act(async () => {
        await Promise.resolve()
      })

      expect(api.scanTools).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: 'Liberar sesión' })).toBeInTheDocument()
      expect(screen.getByText(/network stop failed/i)).toBeInTheDocument()
      vi.useRealTimers()
    })
  })
})
