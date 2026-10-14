import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SystemPanel } from './SystemPanel'
import type { Credentials } from '../api'
import type { ConfigResponse, SystemReadiness } from '../types'

const api = vi.hoisted(() => ({
  getReadiness: vi.fn(),
  setSystemMode: vi.fn(),
  applyConfig: vi.fn(),
  downloadModelInstructions: vi.fn(),
  rollbackConfig: vi.fn(),
  exportConfig: vi.fn(),
  importConfig: vi.fn(),
}))
vi.mock('../api', () => api)

const mockAdminCredentials: Credentials = {
  sessionId: 'admin-sess',
  ticket: 'admin-ticket',
  role: 'admin',
}

const mockReaderCredentials: Credentials = {
  sessionId: 'reader-sess',
  ticket: 'reader-ticket',
  role: 'reader',
}

const mockInitialReadiness: SystemReadiness = {
  backend_ok: true,
  python_version: '3.11.9',
  torch_available: true,
  cuda_available: true,
  gpu_name: 'NVIDIA GeForce RTX 4070',
  vram_free_mb: 8192,
  model_present: false,
  model_status: 'missing_weights',
  inventory_ready: false,
  gallery_ready: false,
  calibration_ready: false,
  arduino_ready: false,
  active_mode: 'mock',
  can_activate_real: false,
  preparation_reasons: [
    'Falta confirmar el inventario de 9 herramientas',
    'Falta calibrar el fondo de la bandeja vacía',
    'Faltan los pesos del modelo Gemma',
  ],
}

const mockConfig: ConfigResponse = {
  saved_version: 3,
  active_version: 2,
  is_dirty: true,
  settings: {
    mode: 'mock',
    gallery_mode: 'text-only',
    text_weight: 0.5,
    classifier_threshold: 0.7,
    classifier_margin: 0.08,
    roi: [0, 0, 100, 100],
    inventory_confirmed: false,
    classifier_profiles: {},
  },
  inventory: {
    example: true,
    categories: [],
  },
}

describe('SystemPanel component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getReadiness.mockResolvedValue(mockInitialReadiness)
  })

  it('renders the 7-point readiness checklist with blockers and GPU info', async () => {
    render(
      <SystemPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
        onNavigateTab={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(api.getReadiness).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })

    expect(screen.getByText('Asistente de Configuración Inicial (7 Puntos)')).toBeInTheDocument()
    expect(screen.getByText(/NVIDIA GeForce RTX 4070/i)).toBeInTheDocument()
    expect(screen.getByText(/Falta confirmar el inventario de 9 herramientas/i)).toBeInTheDocument()
  })

  it('navigates to relevant tabs when clicking checklist action buttons', async () => {
    const onNavigateTab = vi.fn()
    render(
      <SystemPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
        onNavigateTab={onNavigateTab}
      />
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Configurar inventario' })).toBeInTheDocument()
    })

    // Click configure inventory -> goes to tools tab
    fireEvent.click(screen.getByRole('button', { name: 'Configurar inventario' }))
    expect(onNavigateTab).toHaveBeenCalledWith('tools')

    // Click calibrate background -> goes to calibration tab
    fireEvent.click(screen.getByRole('button', { name: 'Calibrar fondo' }))
    expect(onNavigateTab).toHaveBeenCalledWith('calibration')

    // Click connect arduino -> goes to arduino tab
    fireEvent.click(screen.getByRole('button', { name: 'Conectar Arduino' }))
    expect(onNavigateTab).toHaveBeenCalledWith('arduino')
  })

  it('executes single Prepare AI action transitioning through preparation and hot apply', async () => {
    const onUpdated = vi.fn()
    const prepReadiness: SystemReadiness = {
      ...mockInitialReadiness,
      active_mode: 'preparation',
    }
    api.setSystemMode.mockResolvedValue({
      mode: 'preparation',
      readiness: prepReadiness,
    })
    api.applyConfig.mockResolvedValue({
      ...mockConfig,
      saved_version: 3,
      active_version: 3,
      is_dirty: false,
    })

    render(
      <SystemPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={onUpdated}
        onNavigateTab={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preparar IA' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Preparar IA' }))

    await waitFor(() => {
      expect(api.setSystemMode).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, 'preparation')
      expect(api.applyConfig).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })
    expect(screen.getByText(/Preparación de IA completada\. Versión v3 activa\./i)).toBeInTheDocument()
  })

  it('displays terminal download instructions for EmbeddingGemma 2 model', async () => {
    api.downloadModelInstructions.mockResolvedValue({
      detail: 'python -m toollighthouse.download_model --variant base',
    })

    render(
      <SystemPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
        onNavigateTab={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Instrucciones' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Instrucciones' }))

    await waitFor(() => {
      expect(api.downloadModelInstructions).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })
    expect(screen.getByText(/python -m toollighthouse\.download_model/i)).toBeInTheDocument()
  })

  it('supports rollback with confirmation dialog', async () => {
    const onUpdated = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.rollbackConfig.mockResolvedValue({
      ...mockConfig,
      saved_version: 2,
      active_version: 2,
      is_dirty: false,
    })

    render(
      <SystemPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={onUpdated}
        onNavigateTab={vi.fn()}
      />
    )

    const rollbackBtn = screen.getByRole('button', { name: /Restaurar versión anterior/i })
    fireEvent.click(rollbackBtn)

    await waitFor(() => {
      expect(api.rollbackConfig).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })
    expect(screen.getByText(/Configuración restaurada con éxito a la versión v2/i)).toBeInTheDocument()
  })

  it('enforces role gates for reader sessions', async () => {
    render(
      <SystemPanel
        apiUrl="http://localhost:8000"
        credentials={mockReaderCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
        onNavigateTab={vi.fn()}
      />
    )

    expect(screen.getByText(/Modo de solo lectura:/i)).toBeInTheDocument()
    const mockModeBtn = screen.getByRole('button', { name: /Modo Mock/i })
    expect(mockModeBtn).toBeDisabled()
  })
})
