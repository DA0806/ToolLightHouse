import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CalibrationView } from './CalibrationView'
import type { Credentials } from '../api'
import type { ConfigResponse } from '../types'

const api = vi.hoisted(() => ({
  getCalibration: vi.fn(),
  getCalibrationBackgroundBlob: vi.fn(),
  getConfig: vi.fn(),
  calibrateBackground: vi.fn(),
  updateRoi: vi.fn(),
  updateConfig: vi.fn(),
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

const mockConfig: ConfigResponse = {
  saved_version: 1,
  active_version: 1,
  is_dirty: false,
  settings: {
    mode: 'mock',
    gallery_mode: 'text-only',
    text_weight: 0.5,
    classifier_threshold: 0.7,
    classifier_margin: 0.08,
    inventory_confirmed: true,
    roi: [100, 100, 800, 600],
    classifier_profiles: {},
  },
  inventory: {
    example: false,
    categories: [],
  },
}

describe('CalibrationView component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getCalibration.mockResolvedValue({
      calibrated: true,
      status: 'ready',
      image: { width: 1280, height: 960, orientation: 'normalized' },
      roi: [100, 100, 800, 600],
    })
    api.getCalibrationBackgroundBlob.mockResolvedValue(new Blob(['fake-jpg'], { type: 'image/jpeg' }))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:tray-bg') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  })

  it('renders initial text-only mode and tray calibration status', async () => {
    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    expect(screen.getByText('Reconocimiento IA y Bandeja Vacía')).toBeInTheDocument()
    expect(screen.getByText('Solo Texto (Predeterminado)')).toBeInTheDocument()
    expect(screen.getByText('Mejorar con fotografías')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('Fondo calibrado')).toBeInTheDocument()
    })
    expect(screen.getByText('1280 × 960 px')).toBeInTheDocument()
  })

  it('toggles hybrid mode and saves draft settings', async () => {
    const onUpdated = vi.fn()
    api.updateConfig.mockResolvedValue({
      ...mockConfig,
      settings: { ...mockConfig.settings, gallery_mode: 'hybrid' },
    })

    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={onUpdated}
      />
    )

    const toggle = screen.getByRole('checkbox', { name: 'Interruptor de modo híbrido' })
    expect(toggle).not.toBeChecked()

    fireEvent.click(toggle)

    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith(
        'http://localhost:8000',
        mockAdminCredentials,
        expect.objectContaining({
          settings: expect.objectContaining({ gallery_mode: 'hybrid' }),
        })
      )
    })
    expect(screen.getByText(/Parámetros de reconocimiento guardados en el borrador/i)).toBeInTheDocument()
  })

  it('adjusts weight slider and saves advanced parameters', async () => {
    const hybridConfig: ConfigResponse = {
      ...mockConfig,
      settings: { ...mockConfig.settings, gallery_mode: 'hybrid', text_weight: 0.6 },
    }
    api.updateConfig.mockResolvedValue(hybridConfig)

    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={hybridConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // Open advanced accordion
    const accordionBtn = screen.getByRole('button', { name: /⌄ Configuración avanzada de clasificación/i })
    fireEvent.click(accordionBtn)

    // Verify hybrid radio is checked
    const hybridRadio = screen.getByLabelText(/Texto \+ imágenes \(híbrido\)/i)
    expect(hybridRadio).toBeChecked()

    // Slider for text weight
    const slider = screen.getByLabelText('Ponderación Texto vs Imágenes')
    fireEvent.change(slider, { target: { value: '0.75' } })

    const saveParamsBtn = screen.getByRole('button', { name: 'Guardar parámetros avanzados' })
    fireEvent.click(saveParamsBtn)

    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith(
        'http://localhost:8000',
        mockAdminCredentials,
        expect.objectContaining({
          settings: expect.objectContaining({ text_weight: 0.75 }),
        })
      )
    })
  })

  it('uploads empty tray background image and refreshes calibration view', async () => {
    api.calibrateBackground.mockResolvedValue({
      status: 'calibrated',
      image: { width: 1280, height: 960, orientation: 'normalized' },
      roi: [50, 50, 700, 500],
    })
    const onServerRefresh = vi.fn()
    api.getConfig.mockResolvedValue({ ...mockConfig, saved_version: 2 })

    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
        onServerConfigRefreshed={onServerRefresh}
      />
    )

    const file = new File(['bg-data'], 'empty_tray.jpg', { type: 'image/jpeg' })
    const fileInputs = document.querySelectorAll('input[type="file"]')
    fireEvent.change(fileInputs[0], { target: { files: [file] } })

    await waitFor(() => {
      expect(api.calibrateBackground).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, file)
      expect(onServerRefresh).toHaveBeenCalledWith(expect.objectContaining({ saved_version: 2 }), [])
    })
    expect(screen.getByText(/Fotografía de fondo de bandeja vacía guardada correctamente/i)).toBeInTheDocument()
  })

  it('updates ROI via numeric inputs and saves coordinates', async () => {
    api.updateRoi.mockResolvedValue({ roi: [120, 150, 850, 620] })
    const onServerRefresh = vi.fn()
    api.getConfig.mockResolvedValue({ ...mockConfig, saved_version: 3, settings: { ...mockConfig.settings, roi: [120, 150, 850, 620] } })

    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
        onServerConfigRefreshed={onServerRefresh}
      />
    )

    await waitFor(() => {
      expect(screen.getByLabelText('Fondo de calibración interactivo para dibujar ROI')).toBeInTheDocument()
    })

    // Numeric inputs for X, Y, Ancho, Alto
    const xInput = screen.getByLabelText('X')
    const yInput = screen.getByLabelText('Y')
    const wInput = screen.getByLabelText('Ancho')
    const hInput = screen.getByLabelText('Alto')

    fireEvent.change(xInput, { target: { value: '120' } })
    fireEvent.change(yInput, { target: { value: '150' } })
    fireEvent.change(wInput, { target: { value: '850' } })
    fireEvent.change(hInput, { target: { value: '620' } })

    const saveRoiBtn = screen.getByRole('button', { name: 'Guardar ROI' })
    fireEvent.click(saveRoiBtn)

    await waitFor(() => {
      expect(api.updateRoi).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, [120, 150, 850, 620])
      expect(onServerRefresh).toHaveBeenCalledWith(expect.objectContaining({ saved_version: 3 }), ['roi'])
    })
    expect(screen.getByText(/Región de interés \(ROI \[120, 150, 850, 620\]\) guardada correctamente/i)).toBeInTheDocument()
  })

  it('preserves full calibrated dimensions when editing X before an ROI exists', async () => {
    api.getCalibration.mockResolvedValue({
      calibrated: true,
      status: 'ready',
      image: { width: 1280, height: 960, orientation: 'normalized' },
      roi: null,
    })
    const configWithoutRoi: ConfigResponse = {
      ...mockConfig,
      settings: { ...mockConfig.settings, roi: null },
    }

    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={configWithoutRoi}
        onConfigUpdated={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(screen.getByLabelText(/dibujar ROI/)).toBeInTheDocument()
    })

    const xInput = screen.getByLabelText('X')
    const widthInput = screen.getByLabelText('Ancho')
    const heightInput = screen.getByLabelText('Alto')
    expect(widthInput).toHaveValue(1280)
    expect(heightInput).toHaveValue(960)

    fireEvent.change(xInput, { target: { value: '20' } })

    expect(xInput).toHaveValue(20)
    expect(widthInput).toHaveValue(1280)
    expect(heightInput).toHaveValue(960)
  })

  it('enforces role gates for reader sessions', async () => {
    render(
      <CalibrationView
        apiUrl="http://localhost:8000"
        credentials={mockReaderCredentials}
        config={mockConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    expect(screen.getByText(/Modo de solo lectura:/i)).toBeInTheDocument()
    expect(screen.getByText(/Se requiere rol Administrador/i)).toBeInTheDocument()
    const toggle = screen.getByRole('checkbox', { name: 'Interruptor de modo híbrido' })
    expect(toggle).toBeDisabled()

    const xInput = screen.getByLabelText('X')
    expect(xInput).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Guardar ROI' })).not.toBeInTheDocument()
  })
})
