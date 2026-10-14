import { useCallback, useEffect, useRef, useState } from 'react'
import {
  allOff,
  connectArduino,
  disconnectArduino,
  enterMaintenance,
  getArduinoPorts,
  getStatus,
  leaveMaintenance,
  testLed,
  type Credentials,
} from '../api'
import type { ArduinoStatus, ComPort, ConfigResponse } from '../types'

function formatAckTimestamp(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 'Sin ACK registrado'
  const date = new Date(value * 1000)
  return Number.isNaN(date.getTime()) ? 'Marca temporal no válida' : date.toLocaleString()
}

interface ArduinoPanelProps {
  apiUrl: string
  credentials: Credentials | null
  config: ConfigResponse | null
  currentArduinoStatus?: ArduinoStatus | null
  maintenanceMode?: boolean
  onMaintenanceChanged?: (inMaintenance: boolean, arduino?: ArduinoStatus) => void
  onStatusUpdated?: (status: ArduinoStatus) => void
}

export function ArduinoPanel({
  apiUrl,
  credentials,
  config,
  currentArduinoStatus,
  maintenanceMode = false,
  onMaintenanceChanged,
  onStatusUpdated,
}: ArduinoPanelProps) {
  const isAdmin = credentials?.role === 'admin'
  const [localMaintenance, setLocalMaintenance] = useState<boolean>(() => Boolean(maintenanceMode))
  useEffect(() => {
    setLocalMaintenance(Boolean(maintenanceMode))
  }, [maintenanceMode])
  const inMaintenance = localMaintenance
  const [ports, setPorts] = useState<ComPort[]>([])
  const [selectedPort, setSelectedPort] = useState<string>('')
  const [useSimulation, setUseSimulation] = useState<boolean>(false)
  const [arduinoState, setArduinoState] = useState<ArduinoStatus | null>(currentArduinoStatus || null)
  const [testingLed, setTestingLed] = useState<number | null>(null)
  const [testCountdown, setTestCountdown] = useState<number>(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const countdownIntervalRef = useRef<number | null>(null)
  const testGenRef = useRef(0)

  // Keep internal arduino state updated from props
  useEffect(() => {
    if (currentArduinoStatus) {
      setArduinoState(currentArduinoStatus)
    }
  }, [currentArduinoStatus])

  // Clear countdown timer on unmount
  useEffect(() => {
    return () => {
      if (countdownIntervalRef.current) {
        window.clearInterval(countdownIntervalRef.current)
      }
    }
  }, [])

  // Enumerate COM ports
  const refreshPorts = useCallback(async () => {
    if (!credentials || !isAdmin) return
    setError(null)
    try {
      const res = await getArduinoPorts(apiUrl, credentials)
      setPorts(res.ports)
      if (res.ports.length > 0 && !selectedPort) {
        setSelectedPort(res.ports[0].port)
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al listar puertos COM')
    }
  }, [apiUrl, credentials, isAdmin, selectedPort])

  useEffect(() => {
    void refreshPorts()
  }, [refreshPorts])

  // Connect / Disconnect
  const handleConnect = async () => {
    if (!credentials || !isAdmin) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const res = await connectArduino(apiUrl, credentials, {
        port: useSimulation ? undefined : selectedPort,
        simulated: useSimulation,
      })
      setArduinoState(res.arduino)
      onStatusUpdated?.(res.arduino)
      setSuccessMessage(
        res.arduino.simulated
          ? 'Conexión simulada con Arduino activada.'
          : `Conectado exitosamente al puerto ${selectedPort}.`
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al conectar al Arduino')
    } finally {
      setBusy(false)
    }
  }

  const handleDisconnect = async () => {
    if (!credentials || !isAdmin) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const res = await disconnectArduino(apiUrl, credentials)
      setArduinoState(res.arduino)
      onStatusUpdated?.(res.arduino)
      setSuccessMessage('Arduino desconectado de forma segura.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al desconectar')
    } finally {
      setBusy(false)
    }
  }

  // Maintenance mode toggle
  const handleToggleMaintenance = async () => {
    if (!credentials || !isAdmin) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      if (inMaintenance) {
        const res = await leaveMaintenance(apiUrl, credentials)
        setLocalMaintenance(false)
        onMaintenanceChanged?.(false, res.arduino)
        setArduinoState(res.arduino)
        onStatusUpdated?.(res.arduino)
        setSuccessMessage('Modo de mantenimiento finalizado. Inferencia visual reactivada.')
      } else {
        const res = await enterMaintenance(apiUrl, credentials)
        setLocalMaintenance(true)
        onMaintenanceChanged?.(true, res.arduino)
        setArduinoState(res.arduino)
        onStatusUpdated?.(res.arduino)
        setSuccessMessage('Modo de mantenimiento activado. Inferencia en espera; pruebas manuales habilitadas.')
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al cambiar modo de mantenimiento')
    } finally {
      setBusy(false)
    }
  }

  // Single LED test with 2s countdown
  const handleTestLed = async (ledIndex: number) => {
    if (!credentials || !isAdmin || !inMaintenance) return
    setError(null)
    setTestingLed(ledIndex)
    setTestCountdown(2.0)

    const thisTestGen = ++testGenRef.current

    if (countdownIntervalRef.current) {
      window.clearInterval(countdownIntervalRef.current)
    }

    try {
      const res = await testLed(apiUrl, credentials, ledIndex, 2.0)
      if (testGenRef.current !== thisTestGen) return
      setArduinoState(res.arduino)
      onStatusUpdated?.(res.arduino)

      // Start countdown display and poll during countdown
      let remaining = 2.0
      let polledMidpoint = false
      countdownIntervalRef.current = window.setInterval(() => {
        remaining -= 0.2
        if (remaining <= 1.0 && !polledMidpoint) {
          polledMidpoint = true
          void getStatus(apiUrl, credentials, 2000).then((latest) => {
            if (testGenRef.current === thisTestGen && latest.arduino) {
              setArduinoState(latest.arduino)
              onStatusUpdated?.(latest.arduino)
            }
          }).catch(() => undefined)
        }
        if (remaining <= 0) {
          if (countdownIntervalRef.current) {
            window.clearInterval(countdownIntervalRef.current)
            countdownIntervalRef.current = null
          }
          setTestingLed(null)
          setTestCountdown(0)
          // Poll authoritative status from backend on expiry to verify ACK is off
          void getStatus(apiUrl, credentials, 3000).then((latest) => {
            if (testGenRef.current === thisTestGen && latest.arduino) {
              setArduinoState(latest.arduino)
              onStatusUpdated?.(latest.arduino)
            }
          }).catch(() => undefined)
        } else {
          setTestCountdown(Math.round(remaining * 10) / 10)
        }
      }, 200)
    } catch (caught) {
      if (testGenRef.current === thisTestGen) {
        setTestingLed(null)
        setTestCountdown(0)
        setError(caught instanceof Error ? caught.message : 'Error al probar LED')
      }
    }
  }

  // Emergency all-off
  const handleAllOff = async () => {
    if (!credentials || !isAdmin || !inMaintenance) return
    testGenRef.current += 1
    if (countdownIntervalRef.current) {
      window.clearInterval(countdownIntervalRef.current)
      countdownIntervalRef.current = null
    }
    setTestingLed(null)
    setTestCountdown(0)
    try {
      const res = await allOff(apiUrl, credentials)
      setArduinoState(res.arduino)
      onStatusUpdated?.(res.arduino)
      setSuccessMessage('Comando MASK:0 enviado: todos los LEDs apagados.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al apagar LEDs')
    }
  }

  const isConnected = Boolean(arduinoState?.connected)
  const isSimulated = Boolean(arduinoState?.simulated)
  const isAckOk = Boolean(arduinoState?.ack)
  const confirmedMask = arduinoState?.confirmed_mask ?? null
  const lastHardwareError = arduinoState?.error?.trim() || null

  // Categories mapping for pins table
  const categories = config?.inventory?.categories || []

  return (
    <section className="arduino-panel-view" aria-label="Diagnóstico de Hardware Arduino">
      <header className="section-header">
        <div>
          <p className="eyebrow">Hardware</p>
          <h2>Diagnóstico y Control de Arduino Nano</h2>
        </div>
      </header>

      {/* Notifications */}
      {error && (
        <div className="alert alert-error" role="alert">
          <strong>Atención:</strong> {error}
        </div>
      )}
      {successMessage && (
        <div className="alert alert-info" role="status">
          {successMessage}
        </div>
      )}
      {!isAdmin && (
        <div className="alert alert-warning" role="status">
          <strong>Modo de solo lectura:</strong> Las operaciones seriales y pruebas de LEDs requieren rol Administrador.
        </div>
      )}

      {/* Connection Controls Card */}
      <article className="card connection-card">
        <div className="card-header">
          <h3>Conexión Serial USB</h3>
          <span className={`badge ${isConnected ? 'badge-ok' : 'badge-error'}`}>
            {isConnected ? (isSimulated ? 'Simulado' : 'Conectado') : 'Desconectado'}
          </span>
        </div>

        <div className="connection-form">
          <div className="form-row">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={useSimulation}
                onChange={(e) => setUseSimulation(e.target.checked)}
                disabled={!isAdmin || isConnected}
              />
              <span>Usar conexión serial simulada (desarrollo sin hardware)</span>
            </label>
          </div>

          {!useSimulation && (
            <div className="port-selection-row">
              <label>
                Puerto COM detectado
                <select
                  value={selectedPort}
                  onChange={(e) => setSelectedPort(e.target.value)}
                  disabled={!isAdmin || isConnected || ports.length === 0}
                  aria-label="Seleccionar puerto COM"
                >
                  {ports.length === 0 ? (
                    <option value="">No se detectaron puertos COM</option>
                  ) : (
                    ports.map((p) => (
                      <option key={p.port} value={p.port}>
                        {p.port} ({p.description || 'Dispositivo USB'})
                      </option>
                    ))
                  )}
                </select>
              </label>
              <button
                type="button"
                className="button button-sm button-secondary"
                onClick={() => void refreshPorts()}
                disabled={!isAdmin || isConnected}
              >
                Actualizar puertos
              </button>
            </div>
          )}

          <div className="connection-buttons">
            {!isConnected ? (
              <button
                type="button"
                className="button button-primary"
                onClick={() => void handleConnect()}
                disabled={!isAdmin || busy || (!useSimulation && !selectedPort)}
              >
                {busy ? 'Conectando…' : 'Conectar Arduino'}
              </button>
            ) : (
              <button
                type="button"
                className="button button-danger"
                onClick={() => void handleDisconnect()}
                disabled={!isAdmin || busy}
              >
                {busy ? 'Desconectando…' : 'Desconectar'}
              </button>
            )}
          </div>
        </div>

        {/* Status display */}
        <div className="status-grid">
          <div className="status-item">
            <span className="status-label">Estado físico</span>
            <strong>{isConnected ? (isSimulated ? 'Simulación en software' : 'Puerto COM activo') : 'Desconectado'}</strong>
          </div>
          <div className="status-item">
            <span className="status-label">Confirmación ACK</span>
            <strong className={isAckOk ? 'text-ok' : 'text-warning'}>
              {isConnected ? (isAckOk ? 'ACK verificado' : 'Sin confirmación') : 'No aplicable'}
            </strong>
          </div>
          <div className="status-item">
            <span className="status-label">Máscara solicitada</span>
            <strong>{arduinoState?.requested_mask !== null && arduinoState?.requested_mask !== undefined ? arduinoState.requested_mask : '—'}</strong>
          </div>
          <div className="status-item">
            <span className="status-label">Máscara confirmada</span>
            <strong>{confirmedMask !== null ? confirmedMask : '—'}</strong>
          </div>
          <div className="status-item">
            <span className="status-label">Último ACK</span>
            <strong>{formatAckTimestamp(arduinoState?.last_ack_at)}</strong>
          </div>
          <div className="status-item">
            <span className="status-label">Error reportado por Arduino</span>
            <strong className={lastHardwareError ? 'text-warning' : 'text-ok'}>
              {lastHardwareError || 'Sin errores reportados'}
            </strong>
          </div>
        </div>
      </article>

      {/* Maintenance Mode & Unit LED Tests Card */}
      <article className="card maintenance-card">
        <div className="card-header">
          <div>
            <h3>Modo de Mantenimiento y Pruebas Individuales</h3>
            <p className="card-subtitle">
              Suspende la inferencia para permitir encender LEDs individuales de forma segura con apagado automático.
            </p>
          </div>
          <span className={`badge ${inMaintenance ? 'badge-warning' : 'badge-info'}`}>
            {inMaintenance ? 'Mantenimiento Activo' : 'Operación Normal'}
          </span>
        </div>

        <div className="maintenance-toggle-row">
          <button
            type="button"
            className={`button ${inMaintenance ? 'button-secondary' : 'button-warning'}`}
            onClick={() => void handleToggleMaintenance()}
            disabled={!isAdmin || !isConnected || busy}
          >
            {inMaintenance ? 'Salir de Modo Mantenimiento' : 'Entrar a Modo Mantenimiento'}
          </button>

          {inMaintenance && (
            <button
              type="button"
              className="button button-danger button-sm"
              onClick={() => void handleAllOff()}
              disabled={!isAdmin}
            >
              Apagar todos (MASK:0)
            </button>
          )}
        </div>

        {!inMaintenance && (
          <p className="maintenance-lock-notice">
            🔒 Para activar las pruebas unitarias de LEDs, debes entrar al Modo de Mantenimiento.
          </p>
        )}

        {/* 9 Individual LED Buttons */}
        <div className="led-test-grid-container">
          <h4>Pruebas Unitarias de LEDs (D2 a D10)</h4>
          <p className="led-test-hint">
            Cada prueba enciende exclusivamente un LED durante 2 segundos con apagado automático garantizado. No se permite encender los 9 simultáneamente.
          </p>

          <div className="led-buttons-grid">
            {Array.from({ length: 9 }, (_, ledIdx) => {
              const pin = ledIdx + 2
              const tool = categories.find((c) => c.led_index === ledIdx)
              const toolName = tool?.display_name || tool?.name || `Herramienta ${ledIdx + 1}`
              const isTestingThis = testingLed === ledIdx
              const isConfirmedOn = confirmedMask !== null && isAckOk && (confirmedMask & (1 << ledIdx)) !== 0
              const isPendingOrUnknown = isTestingThis && (confirmedMask === null || !isAckOk)

              return (
                <div key={ledIdx} className={`led-test-box ${isConfirmedOn ? 'led-active' : isPendingOrUnknown ? 'led-waiting' : ''}`}>
                  <div className="led-box-top">
                    <span className={`led-dot ${isConfirmedOn ? 'led-on' : isPendingOrUnknown ? 'led-pending' : ''}`} aria-hidden="true" />
                    <strong>LED {ledIdx} · D{pin}</strong>
                  </div>
                  <span className="led-tool-label">
                    {isTestingThis
                      ? isConfirmedOn
                        ? `${toolName} · ON verificado`
                        : isPendingOrUnknown
                        ? `${toolName} · Esperando ACK...`
                        : `${toolName} · Apagado`
                      : toolName}
                  </span>
                  <button
                    type="button"
                    className={`button button-sm ${isTestingThis ? 'button-accent' : 'button-secondary'}`}
                    onClick={() => void handleTestLed(ledIdx)}
                    disabled={!isAdmin || !inMaintenance || !isConnected}
                    aria-label={`Probar LED ${ledIdx} (Pin D${pin})`}
                  >
                    {isTestingThis ? `Probando (${testCountdown}s)` : 'Probar LED'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      </article>

      {/* Pin Mapping Reference Table */}
      <article className="card pin-table-card">
        <div className="card-header">
          <h3>Mapeo Físico de Pines a Herramientas</h3>
        </div>
        <div className="table-responsive">
          <table className="pin-table">
            <thead>
              <tr>
                <th>LED Bit</th>
                <th>Pin Físico Nano</th>
                <th>Herramienta Asignada</th>
                <th>Ubicación</th>
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: 9 }, (_, idx) => {
                const tool = categories.find((c) => c.led_index === idx)
                return (
                  <tr key={idx}>
                    <td><code>Bit {idx}</code></td>
                    <td><code>D{idx + 2}</code></td>
                    <td><strong>{tool?.display_name || tool?.name || 'Sin asignar'}</strong></td>
                    <td>{tool?.location || 'Por confirmar'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </article>

      {/* Firmware & Troubleshooting Guide */}
      <article className="card firmware-guide-card">
        <div className="card-header">
          <h3>Guía de Instalación del Firmware (Arduino IDE)</h3>
        </div>
        <div className="firmware-guide-body">
          <p>
            El firmware debe cargarse manualmente mediante Arduino IDE. No se realiza programación automática desde el navegador.
          </p>
          <ol>
            <li>
              Abre <strong>Arduino IDE</strong> en la laptop y carga el archivo:
              <br />
              <code>firmware/nano/nano.ino</code>
            </li>
            <li>
              En el menú <strong>Herramientas → Placa</strong>, selecciona: <strong>Arduino Nano</strong>.
            </li>
            <li>
              En <strong>Herramientas → Procesador</strong>, selecciona: <strong>ATmega328P</strong>.
              <br />
              <em>Nota:</em> Si la carga falla con error de timeout o <code>stk500_recv()</code>, selecciona la opción <strong>ATmega328P (Old Bootloader)</strong>.
            </li>
            <li>
              Verifica el puerto en <strong>Herramientas → Puerto</strong> y presiona <strong>Subir</strong> (Ctrl+U).
            </li>
            <li>
              El firmware opera a <strong>115200 baudios</strong> con protocolo <code>MASK:N / ACK:N</code> y watchdog de seguridad de 2500 ms.
            </li>
          </ol>
        </div>
      </article>
    </section>
  )
}
