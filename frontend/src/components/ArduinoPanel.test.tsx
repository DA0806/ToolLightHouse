import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ArduinoPanel } from './ArduinoPanel'
import type { Credentials } from '../api'
import type { ArduinoStatus, ConfigResponse } from '../types'

const api = vi.hoisted(() => ({
  getArduinoPorts: vi.fn(),
  connectArduino: vi.fn(),
  disconnectArduino: vi.fn(),
  enterMaintenance: vi.fn(),
  leaveMaintenance: vi.fn(),
  testLed: vi.fn(),
  allOff: vi.fn(),
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
    roi: [0, 0, 100, 100],
    inventory_confirmed: true,
    classifier_profiles: {},
  },
  inventory: {
    example: false,
    categories: Array.from({ length: 9 }, (_, idx) => ({
      class_id: `tool_${idx + 1}`,
      name: `tool_${idx + 1}`,
      display_name: `Herramienta ${idx + 1}`,
      descriptions: ['desc'],
      location: `A${idx + 1}`,
      led_index: idx,
      pin: idx + 2,
    })),
  },
}

describe('ArduinoPanel component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getArduinoPorts.mockResolvedValue({
      ports: [
        { port: 'COM3', description: 'Arduino Nano CH340' },
        { port: 'COM5', description: 'USB Serial Port' },
      ],
    })
  })

  it('enumerates COM ports and connects to selected physical port', async () => {
    const connectedStatus: ArduinoStatus = {
      connected: true,
      simulated: false,
      port: 'COM3',
      requested_mask: 0,
      confirmed_mask: 0,
      ack: true,
      last_ack_at: 1700000000,
      error: 'ACK timeout recovered',
    }
    api.connectArduino.mockResolvedValue({
      message: 'Connected',
      arduino: connectedStatus,
    })

    render(
      <ArduinoPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
      />
    )

    await waitFor(() => {
      expect(api.getArduinoPorts).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })

    // Selected port dropdown
    const select = screen.getByLabelText('Seleccionar puerto COM')
    expect(select).toHaveValue('COM3')

    // Click connect
    const connectBtn = screen.getByRole('button', { name: 'Conectar Arduino' })
    fireEvent.click(connectBtn)

    await waitFor(() => {
      expect(api.connectArduino).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, {
        port: 'COM3',
        simulated: false,
      })
    })

    expect(screen.getByText(/Conectado exitosamente al puerto COM3/i)).toBeInTheDocument()
    expect(screen.getByText('Puerto COM activo')).toBeInTheDocument()
    expect(screen.getByText('ACK verificado')).toBeInTheDocument()
    expect(screen.getByText(new Date(1700000000 * 1000).toLocaleString())).toBeInTheDocument()
    expect(screen.getByText('ACK timeout recovered')).toBeInTheDocument()
  })

  it('supports simulated connection mode without physical COM port', async () => {
    const simStatus: ArduinoStatus = {
      connected: true,
      simulated: true,
      requested_mask: 0,
      confirmed_mask: 0,
      ack: true,
    }
    api.connectArduino.mockResolvedValue({
      message: 'Simulated connection active',
      arduino: simStatus,
    })

    render(
      <ArduinoPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
      />
    )

    // Check simulated checkbox
    const simCheckbox = screen.getByRole('checkbox', { name: /Usar conexión serial simulada/i })
    fireEvent.click(simCheckbox)
    expect(simCheckbox).toBeChecked()

    // Connect
    const connectBtn = screen.getByRole('button', { name: 'Conectar Arduino' })
    fireEvent.click(connectBtn)

    await waitFor(() => {
      expect(api.connectArduino).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, {
        port: undefined,
        simulated: true,
      })
    })
    expect(screen.getByText(/Conexión simulada con Arduino activada/i)).toBeInTheDocument()
    expect(screen.getByText('Simulación en software')).toBeInTheDocument()
  })

  it('locks individual LED tests until maintenance mode is active', async () => {
    const connectedStatus: ArduinoStatus = {
      connected: true,
      simulated: true,
      requested_mask: 0,
      confirmed_mask: 0,
      ack: true,
    }

    render(
      <ArduinoPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        currentArduinoStatus={connectedStatus}
      />
    )

    // Maintenance is initially inactive
    expect(screen.getByText(/Para activar las pruebas unitarias de LEDs, debes entrar al Modo de Mantenimiento/i)).toBeInTheDocument()
    const ledButtons = screen.getAllByRole('button', { name: /Probar LED \d/i })
    expect(ledButtons).toHaveLength(9)
    expect(ledButtons[0]).toBeDisabled()

    // Enter maintenance mode
    api.enterMaintenance.mockResolvedValue({
      maintenance_mode: true,
      arduino: connectedStatus,
    })

    const enterMaintBtn = screen.getByRole('button', { name: 'Entrar a Modo Mantenimiento' })
    fireEvent.click(enterMaintBtn)

    await waitFor(() => {
      expect(api.enterMaintenance).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })
    expect(screen.getByText('Mantenimiento Activo')).toBeInTheDocument()

    // LED buttons should now be enabled
    expect(ledButtons[0]).not.toBeDisabled()
  })

  it('executes exactly one-bit LED test and triggers all-off (MASK:0)', async () => {
    const connectedStatus: ArduinoStatus = {
      connected: true,
      simulated: true,
      requested_mask: 1,
      confirmed_mask: 1,
      ack: true,
    }

    api.enterMaintenance.mockResolvedValue({
      maintenance_mode: true,
      arduino: connectedStatus,
    })
    api.testLed.mockResolvedValue({
      message: 'LED 0 activated for 2.0s',
      led_index: 0,
      pin: 2,
      duration_seconds: 2.0,
      arduino: connectedStatus,
    })
    api.allOff.mockResolvedValue({
      message: 'All LEDs off',
      arduino: { ...connectedStatus, requested_mask: 0, confirmed_mask: 0 },
    })

    render(
      <ArduinoPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
        currentArduinoStatus={connectedStatus}
      />
    )

    // Enter maintenance mode
    fireEvent.click(screen.getByRole('button', { name: 'Entrar a Modo Mantenimiento' }))
    await waitFor(() => expect(screen.getByText('Mantenimiento Activo')).toBeInTheDocument())

    // Click test for LED 0
    const led0Btn = screen.getByRole('button', { name: 'Probar LED 0 (Pin D2)' })
    fireEvent.click(led0Btn)

    await waitFor(() => {
      expect(api.testLed).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials, 0, 2.0)
    })

    // Click "Apagar todos (MASK:0)"
    const allOffBtn = screen.getByRole('button', { name: 'Apagar todos (MASK:0)' })
    fireEvent.click(allOffBtn)

    await waitFor(() => {
      expect(api.allOff).toHaveBeenCalledWith('http://localhost:8000', mockAdminCredentials)
    })
    expect(screen.getByText(/Comando MASK:0 enviado: todos los LEDs apagados/i)).toBeInTheDocument()
  })

  it('displays D2..D10 pin mapping table and Arduino IDE firmware upload instructions', () => {
    render(
      <ArduinoPanel
        apiUrl="http://localhost:8000"
        credentials={mockAdminCredentials}
        config={mockConfig}
      />
    )

    // Check table headers & pin rows
    expect(screen.getByText('Mapeo Físico de Pines a Herramientas')).toBeInTheDocument()
    expect(screen.getByText('Bit 0')).toBeInTheDocument()
    expect(screen.getByText('D2')).toBeInTheDocument()
    expect(screen.getByText('Bit 8')).toBeInTheDocument()
    expect(screen.getByText('D10')).toBeInTheDocument()

    // Firmware instructions
    expect(screen.getByText('Guía de Instalación del Firmware (Arduino IDE)')).toBeInTheDocument()
    expect(screen.getByText(/firmware\/nano\/nano\.ino/i)).toBeInTheDocument()
    expect(screen.getByText(/ATmega328P \(Old Bootloader\)/i)).toBeInTheDocument()
  })

  it('enforces role gates for reader sessions', () => {
    render(
      <ArduinoPanel
        apiUrl="http://localhost:8000"
        credentials={mockReaderCredentials}
        config={mockConfig}
      />
    )

    expect(screen.getByText(/Modo de solo lectura:/i)).toBeInTheDocument()
    expect(screen.getByText(/Las operaciones seriales y pruebas de LEDs requieren rol Administrador/i)).toBeInTheDocument()

    const connectBtn = screen.getByRole('button', { name: 'Conectar Arduino' })
    expect(connectBtn).toBeDisabled()
  })
})
