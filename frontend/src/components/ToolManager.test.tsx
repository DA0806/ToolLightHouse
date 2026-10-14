import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ToolManager } from './ToolManager'
import type { Credentials } from '../api'
import type { ConfigResponse } from '../types'

const api = vi.hoisted(() => ({
  getConfig: vi.fn(),
  updateConfig: vi.fn(),
  applyConfig: vi.fn(),
  listReferences: vi.fn(),
  uploadReference: vi.fn(),
  deleteReference: vi.fn(),
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

const baseCategories = Array.from({ length: 9 }, (_, idx) => ({
  class_id: `tool_${idx + 1}`,
  name: `tool_${idx + 1}`,
  display_name: `Herramienta ${idx + 1}`,
  descriptions: [`Descripción visual de prueba ${idx + 1}`],
  location: `Casillero ${idx + 1}`,
  led_index: idx,
  pin: idx + 2,
  reference_count: 0,
}))

const mockInitialConfig: ConfigResponse = {
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
    roi: [10, 10, 80, 80],
    classifier_profiles: {},
  },
  inventory: {
    example: true,
    categories: baseCategories,
  },
}

describe('ToolManager component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getConfig.mockResolvedValue(mockInitialConfig)
  })

  it('renders all nine tool cards and physical 3x3 organizer grid', () => {
    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // Check 3x3 grid
    expect(screen.getByRole('grid', { name: 'Cuadrícula del organizador' })).toBeInTheDocument()
    const gridButtons = screen.getAllByRole('button', { name: /LED \d, Pin D\d+/i })
    expect(gridButtons).toHaveLength(9)

    // Check 9 tool cards
    for (let i = 1; i <= 9; i++) {
      expect(screen.getByText(`tool_${i}`)).toBeInTheDocument()
      expect(screen.getByDisplayValue(`Herramienta ${i}`)).toBeInTheDocument()
    }
  })

  it('performs atomic LED swap without duplicate assignments', async () => {
    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // tool_1 initially has LED 0, tool_2 has LED 1
    const ledSelects = screen.getAllByLabelText('Selector de LED')
    expect((ledSelects[0] as HTMLSelectElement).value).toBe('0')
    expect((ledSelects[1] as HTMLSelectElement).value).toBe('1')

    // Change tool_1 from LED 0 to LED 1
    fireEvent.change(ledSelects[0], { target: { value: '1' } })

    // tool_1 should now be 1, and tool_2 should have swapped to 0
    expect((ledSelects[0] as HTMLSelectElement).value).toBe('1')
    expect((ledSelects[1] as HTMLSelectElement).value).toBe('0')

    // Verify all 9 LED values are unique (0..8)
    const currentValues = ledSelects.map((sel) => (sel as HTMLSelectElement).value)
    const uniqueValues = new Set(currentValues)
    expect(uniqueValues.size).toBe(9)
  })

  it('allows adding and removing visual descriptions', () => {
    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // Initially tool_1 has 1 description
    expect(screen.getByText('Descripción visual de prueba 1')).toBeInTheDocument()

    // Add a new description to tool_1
    const addInputs = screen.getAllByPlaceholderText(/Nuevo rasgo visible/i)
    fireEvent.change(addInputs[0], { target: { value: 'Mango de goma negra estriada' } })

    const addButtons = screen.getAllByRole('button', { name: 'Añadir' })
    fireEvent.click(addButtons[0])

    expect(screen.getByText('Mango de goma negra estriada')).toBeInTheDocument()

    // Remove the first description
    const removeBtn = screen.getByRole('button', { name: 'Eliminar rasgo: Descripción visual de prueba 1' })
    fireEvent.click(removeBtn)

    expect(screen.queryByText('Descripción visual de prueba 1')).not.toBeInTheDocument()
    expect(screen.getByText('Mango de goma negra estriada')).toBeInTheDocument()
  })

  it('confirms real inventory and converts example state to real', () => {
    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    expect(screen.getByText('Inventario de ejemplo / pendiente')).toBeInTheDocument()
    const confirmBtn = screen.getByRole('button', { name: 'Confirmar inventario real' })
    fireEvent.click(confirmBtn)

    expect(screen.getByText('Inventario real confirmado')).toBeInTheDocument()
    expect(screen.getByText(/Inventario marcado como confirmado/i)).toBeInTheDocument()
  })

  it('disables mutation actions for reader sessions', () => {
    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockReaderCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    expect(screen.getByText(/Modo de solo lectura/i)).toBeInTheDocument()
    const nameInputs = screen.getAllByPlaceholderText(/Ej\. Martillo de uña/i)
    expect(nameInputs[0]).toBeDisabled()

    const saveBtn = screen.getByRole('button', { name: 'Guardar borrador' })
    expect(saveBtn).toBeDisabled()

    const applyBtn = screen.getByRole('button', { name: 'Guardar y aplicar' })
    expect(applyBtn).toBeDisabled()
  })

  it('handles save and apply with version increment and error handling', async () => {
    const onUpdated = vi.fn()
    api.updateConfig.mockResolvedValue({
      ...mockInitialConfig,
      saved_version: 2,
      is_dirty: true,
    })
    api.applyConfig.mockResolvedValue({
      ...mockInitialConfig,
      saved_version: 2,
      active_version: 2,
      is_dirty: false,
    })

    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={onUpdated}
      />
    )

    // Make an edit to mark draft dirty
    const displayInputs = screen.getAllByPlaceholderText(/Ej\. Martillo de uña/i)
    fireEvent.change(displayInputs[0], { target: { value: 'Destornillador Phillips' } })

    const saveBtn = screen.getByRole('button', { name: 'Guardar borrador' })
    expect(saveBtn).not.toBeDisabled()
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(api.updateConfig).toHaveBeenCalledWith(
        'http://localhost:8000',
        mockAdminCredentials,
        expect.objectContaining({
          expected_version: 1,
          inventory: expect.objectContaining({
            categories: expect.arrayContaining([
              expect.objectContaining({ display_name: 'Destornillador Phillips' }),
            ]),
          }),
        })
      )
    })
    expect(screen.getByText(/Configuración guardada \(v2\)/i)).toBeInTheDocument()

    // Test hot-apply
    const applyBtn = screen.getByRole('button', { name: 'Guardar y aplicar' })
    fireEvent.click(applyBtn)

    await waitFor(() => {
      expect(api.applyConfig).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })
    expect(screen.getByText(/Configuración aplicada en caliente con éxito \(v2 activa\)/i)).toBeInTheDocument()
  })

  it('detects 409 conflict and offers reload without silent overwrite', async () => {
    const error409 = Object.assign(new Error('Version mismatch'), { status: 409 })
    api.updateConfig.mockRejectedValue(error409)

    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // Edit tool
    const displayInputs = screen.getAllByPlaceholderText(/Ej\. Martillo de uña/i)
    fireEvent.change(displayInputs[0], { target: { value: 'Alicate de corte' } })

    fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }))

    await waitFor(() => {
      expect(screen.getByText(/Conflicto de versiones: la configuración en el servidor cambió/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /Recargar desde el servidor/i })).toBeInTheDocument()
  })

  it('manages reference photos: open panel, upload sequential files, delete photo', async () => {
    api.listReferences.mockResolvedValue({
      references: [
        { image_id: 'img-1', thumbnail_data_url: 'data:image/jpeg;base64,thumb1' },
      ],
    })
    api.uploadReference.mockResolvedValue({
      image_id: 'img-2',
      thumbnail_data_url: 'data:image/jpeg;base64,thumb2',
    })
    api.deleteReference.mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )

    // Toggle photos for tool_1
    const toggleButtons = screen.getAllByRole('button', { name: /Ver \/ Subir fotografías/i })
    fireEvent.click(toggleButtons[0])

    await waitFor(() => {
      expect(api.listReferences).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, 'tool_1')
    })
    expect(screen.getByRole('img', { name: 'Referencia visual' })).toBeInTheDocument()

    // Upload a photo
    const file = new File(['dummy'], 'ref.jpg', { type: 'image/jpeg' })
    const fileInputs = document.querySelectorAll('input[type="file"]')
    fireEvent.change(fileInputs[0], { target: { files: [file] } })

    await waitFor(() => {
      expect(api.uploadReference).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, 'tool_1', file)
    })
    expect(screen.getByText(/1 foto\(s\) subida\(s\) correctamente/i)).toBeInTheDocument()

    // Delete photo
    const deleteBtns = screen.getAllByRole('button', { name: 'Eliminar foto' })
    fireEvent.click(deleteBtns[0])

    await waitFor(() => {
      expect(api.deleteReference).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, 'tool_1', 'img-1')
    })
    expect(screen.getByText('Fotografía eliminada.')).toBeInTheDocument()
  })
  it('copies the live FileList before resetting the input for browser uploads', async () => {
    api.listReferences.mockResolvedValue({ references: [] })
    api.uploadReference.mockResolvedValue({
      image_id: 'img-live-list',
      thumbnail_data_url: 'data:image/jpeg;base64,thumb-live-list',
    })

    render(
      <ToolManager
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockInitialConfig}
        onConfigUpdated={vi.fn()}
      />
    )
    fireEvent.click(screen.getAllByRole('button', { name: /Ver \/ Subir fotograf/i })[0])
    await waitFor(() => expect(screen.getByText(/Subir archivos/i)).toBeInTheDocument())

    const input = document.querySelector('input[type="file"][multiple]') as HTMLInputElement
    const file = new File(['photo'], 'live-list.jpg', { type: 'image/jpeg' })
    let hasFiles = true
    const liveFileList = {
      0: file,
      get length() { return hasFiles ? 1 : 0 },
    }
    Object.defineProperty(input, 'files', {
      configurable: true,
      get: () => liveFileList,
    })
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => hasFiles ? 'C:\\fakepath\\live-list.jpg' : '',
      set: () => { hasFiles = false },
    })

    fireEvent.change(input)

    await waitFor(() => {
      expect(api.uploadReference).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, 'tool_1', file)
    })
    expect(input.value).toBe('')
  })
})
