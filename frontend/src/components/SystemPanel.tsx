import { useCallback, useEffect, useState } from 'react'
import {
  applyConfig,
  downloadModelInstructions,
  exportConfig,
  getReadiness,
  importConfig,
  rollbackConfig,
  setSystemMode,
  type Credentials,
} from '../api'
import type { ConfigResponse, ConfigTask, SystemReadiness } from '../types'

interface SystemPanelProps {
  apiUrl: string
  credentials: Credentials | null
  config: ConfigResponse | null
  task?: ConfigTask | null
  onConfigUpdated: (config: ConfigResponse) => void
  onNavigateTab: (tab: 'inspection' | 'tools' | 'calibration' | 'arduino' | 'system') => void
}

export function SystemPanel({
  apiUrl,
  credentials,
  config,
  task = null,
  onConfigUpdated,
  onNavigateTab,
}: SystemPanelProps) {
  const isAdmin = credentials?.role === 'admin'
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null)
  const [modelInstructions, setModelInstructions] = useState<string | null>(null)
  const [importPreview, setImportPreview] = useState<Record<string, unknown> | null>(null)
  const [importFileName, setImportFileName] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  // Fetch system readiness
  const refreshReadiness = useCallback(async () => {
    if (!credentials) return
    try {
      const res = await getReadiness(apiUrl, credentials)
      setReadiness(res)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al consultar estado del sistema')
    }
  }, [apiUrl, credentials])

  useEffect(() => {
    void refreshReadiness()
  }, [refreshReadiness])

  // Mode switcher
  const handleSwitchMode = async (targetMode: 'mock' | 'preparation' | 'real') => {
    if (!credentials || !isAdmin) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const res = await setSystemMode(apiUrl, credentials, targetMode)
      setReadiness(res.readiness)
      setSuccessMessage(`Modo cambiado exitosamente a: ${targetMode}.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo cambiar el modo')
    } finally {
      setBusy(false)
    }
  }

  // Model download instructions
  const handleShowModelInstructions = async () => {
    if (!credentials || !isAdmin) return
    setError(null)
    try {
      const res = await downloadModelInstructions(apiUrl, credentials)
      setModelInstructions(res.detail)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al obtener instrucciones del modelo')
    }
  }

  // Single Prepare AI action
  const handlePrepareAi = async () => {
    if (!credentials || !isAdmin || !config) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      // 1. If currently mock and user wants to prepare, switch to preparation mode first
      if (readiness?.active_mode === 'mock') {
        const modeRes = await setSystemMode(apiUrl, credentials, 'preparation')
        setReadiness(modeRes.readiness)
      }
      // 2. Trigger candidate gallery build and hot apply
      const res = await applyConfig(apiUrl, credentials)
      onConfigUpdated(res)
      setSuccessMessage(`Preparación de IA completada. Versión v${res.active_version} activa.`)
      await refreshReadiness()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al preparar IA')
    } finally {
      setBusy(false)
    }
  }

  // Rollback configuration
  const handleRollback = async () => {
    if (!credentials || !isAdmin) return
    if (!window.confirm('¿Deseas restaurar la configuración anterior desde la copia de respaldo?')) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const res = await rollbackConfig(apiUrl, credentials)
      onConfigUpdated(res)
      setSuccessMessage(`Configuración restaurada con éxito a la versión v${res.active_version}.`)
      await refreshReadiness()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al restaurar configuración')
    } finally {
      setBusy(false)
    }
  }

  // Export portable configuration
  const handleExport = async () => {
    if (!credentials || !isAdmin) return
    setError(null)
    try {
      const data = await exportConfig(apiUrl, credentials)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `toollighthouse-config-v${config?.saved_version || 1}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setSuccessMessage('Configuración exportada correctamente. (Nota: Las fotos de referencia locales no se incluyen).')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al exportar configuración')
    }
  }

  // Import portable configuration
  const handleSelectImportFile = (file: File | null) => {
    if (!file) return
    setImportFileName(file.name)
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const parsed = JSON.parse(reader.result as string) as Record<string, unknown>
        if (!parsed || typeof parsed !== 'object') throw new Error('El archivo no contiene un JSON válido.')
        setImportPreview(parsed)
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'Archivo JSON inválido')
        setImportPreview(null)
      }
    }
    reader.readAsText(file)
  }

  const handleConfirmImport = async () => {
    if (!credentials || !isAdmin || !importPreview) return
    setBusy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const res = await importConfig(apiUrl, credentials, importPreview)
      onConfigUpdated(res)
      setImportPreview(null)
      setImportFileName(null)
      setSuccessMessage(`Configuración importada exitosamente (v${res.saved_version} guardada).`)
      await refreshReadiness()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al importar configuración')
    } finally {
      setBusy(false)
    }
  }

  const isModelReady = readiness?.model_status === 'ready'
  const isInventoryReady = Boolean(readiness?.inventory_ready)
  const isGalleryReady = Boolean(readiness?.gallery_ready)
  const isCalibrationReady = Boolean(readiness?.calibration_ready)
  const isArduinoReady = Boolean(readiness?.arduino_ready)
  const canActivateReal = Boolean(readiness?.can_activate_real)

  return (
    <section className="system-panel-view" aria-label="Diagnóstico del Sistema y Asistente de Instalación">
      <header className="section-header">
        <div>
          <p className="eyebrow">Sistema</p>
          <h2>Diagnóstico, Asistente y Modos de Ejecución</h2>
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
          <strong>Modo de solo lectura:</strong> Las acciones del sistema (cambio de modo, importación/exportación y rollback) requieren rol Administrador.
        </div>
      )}

      {/* First-Run Setup Checklist */}
      <article className="card setup-wizard-card">
        <div className="card-header">
          <div>
            <h3>Asistente de Configuración Inicial (7 Puntos)</h3>
            <p className="card-subtitle">
              Verifica los requisitos del sistema para pasar de simulación al reconocimiento real.
            </p>
          </div>
          <button
            type="button"
            className="button button-sm button-secondary"
            onClick={() => void refreshReadiness()}
          >
            Actualizar estado
          </button>
        </div>

        <ul className="setup-checklist" role="list">
          {/* 1. Backend */}
          <li className="checklist-item">
            <span className="check-icon ok" aria-hidden="true">✓</span>
            <div className="check-details">
              <strong>1. Servidor Backend FastAPI</strong>
              <span>Conexión activa con el proceso Python local.</span>
            </div>
            <span className="badge badge-ok">Listo</span>
          </li>

          {/* 2. Python / PyTorch / CUDA */}
          <li className="checklist-item">
            <span className={`check-icon ${readiness?.torch_available ? 'ok' : 'warning'}`} aria-hidden="true">
              {readiness?.torch_available ? '✓' : '⚠'}
            </span>
            <div className="check-details">
              <strong>2. Entorno Python, PyTorch y GPU</strong>
              <span>
                Python {readiness?.python_version || '—'} ·{' '}
                {readiness?.cuda_available
                  ? `CUDA disponible (${readiness.gpu_name || 'GPU'}, ${readiness.vram_free_mb ?? '—'} MB VRAM libre)`
                  : readiness?.torch_available
                  ? 'PyTorch en CPU (sin aceleración CUDA)'
                  : 'PyTorch no instalado en el backend'}
              </span>
            </div>
            <span className={`badge ${readiness?.cuda_available ? 'badge-ok' : 'badge-warning'}`}>
              {readiness?.cuda_available ? 'GPU / CUDA' : 'CPU / Mock'}
            </span>
          </li>

          {/* 3. EmbeddingGemma 2 Model */}
          <li className="checklist-item">
            <span className={`check-icon ${isModelReady ? 'ok' : 'warning'}`} aria-hidden="true">
              {isModelReady ? '✓' : '⚠'}
            </span>
            <div className="check-details">
              <strong>3. Modelo EmbeddingGemma 2</strong>
              <span>
                {isModelReady
                  ? 'Pesos del modelo presentes en models/embeddinggemma-2.'
                  : readiness?.model_status === 'missing_dependency'
                  ? 'Falta instalar PyTorch para cargar el modelo.'
                  : 'Pesos del modelo no encontrados en models/embeddinggemma-2.'}
              </span>
            </div>
            {isModelReady ? (
              <span className="badge badge-ok">Listo</span>
            ) : (
              <button
                type="button"
                className="button button-sm button-secondary"
                onClick={() => void handleShowModelInstructions()}
                disabled={!isAdmin}
              >
                Instrucciones
              </button>
            )}
          </li>

          {/* 4. Physical Inventory */}
          <li className="checklist-item">
            <span className={`check-icon ${isInventoryReady ? 'ok' : 'warning'}`} aria-hidden="true">
              {isInventoryReady ? '✓' : '⚠'}
            </span>
            <div className="check-details">
              <strong>4. Inventario de 9 Herramientas</strong>
              <span>
                {isInventoryReady
                  ? 'Inventario confirmado para el organizador real.'
                  : 'El inventario actual es de ejemplo o no ha sido confirmado.'}
              </span>
            </div>
            {isInventoryReady ? (
              <span className="badge badge-ok">Confirmado</span>
            ) : (
              <button
                type="button"
                className="button button-sm button-primary"
                onClick={() => onNavigateTab('tools')}
              >
                Configurar inventario
              </button>
            )}
          </li>

          {/* 5. AI Gallery */}
          <li className="checklist-item">
            <span className={`check-icon ${isGalleryReady ? 'ok' : 'warning'}`} aria-hidden="true">
              {isGalleryReady ? '✓' : '⚠'}
            </span>
            <div className="check-details">
              <strong>5. Galería de Embeddings</strong>
              <span>
                {isGalleryReady
                  ? 'Caché de embeddings compilada y sincronizada.'
                  : 'La galería no ha sido compilada o está desactualizada.'}
              </span>
            </div>
            {isGalleryReady ? (
              <span className="badge badge-ok">Lista</span>
            ) : (
              <button
                type="button"
                className="button button-sm button-accent"
                onClick={() => void handlePrepareAi()}
                disabled={!isAdmin || busy}
              >
                Preparar IA
              </button>
            )}
          </li>

          {/* 6. Tray Calibration & ROI */}
          <li className="checklist-item">
            <span className={`check-icon ${isCalibrationReady ? 'ok' : 'warning'}`} aria-hidden="true">
              {isCalibrationReady ? '✓' : '⚠'}
            </span>
            <div className="check-details">
              <strong>6. Calibración de Fondo y ROI</strong>
              <span>
                {isCalibrationReady
                  ? 'Fondo de bandeja vacía guardado y ROI dentro de límites.'
                  : 'Falta capturar la fotografía de fondo de la bandeja vacía.'}
              </span>
            </div>
            {isCalibrationReady ? (
              <span className="badge badge-ok">Calibrado</span>
            ) : (
              <button
                type="button"
                className="button button-sm button-primary"
                onClick={() => onNavigateTab('calibration')}
              >
                Calibrar fondo
              </button>
            )}
          </li>

          {/* 7. Arduino Nano */}
          <li className="checklist-item">
            <span className={`check-icon ${isArduinoReady ? 'ok' : 'warning'}`} aria-hidden="true">
              {isArduinoReady ? '✓' : '⚠'}
            </span>
            <div className="check-details">
              <strong>7. Arduino Nano</strong>
              <span>
                {isArduinoReady
                  ? 'Conexión activa con el microcontrolador (o simulación seleccionada).'
                  : 'Sin conexión serial con el microcontrolador ni simulación activa.'}
              </span>
            </div>
            {isArduinoReady ? (
              <span className="badge badge-ok">Listo</span>
            ) : (
              <button
                type="button"
                className="button button-sm button-primary"
                onClick={() => onNavigateTab('arduino')}
              >
                Conectar Arduino
              </button>
            )}
          </li>
        </ul>

        {/* Model Instructions Modal/Card if toggled */}
        {modelInstructions && (
          <div className="model-instructions-panel">
            <h4>Instrucciones para Descargar el Modelo en la Laptop</h4>
            <p>
              El modelo <strong>EmbeddingGemma 2</strong> requiere aproximadamente 6 GB de espacio y debe descargarse en la laptop mediante la terminal:
            </p>
            <pre className="code-block">{modelInstructions}</pre>
            <p className="instructions-note">
              <em>Nota:</em> Por razones de seguridad y límites del navegador, la descarga no se ejecuta en segundo plano desde el teléfono.
            </p>
            <button
              type="button"
              className="button button-sm button-secondary"
              onClick={() => setModelInstructions(null)}
            >
              Cerrar instrucciones
            </button>
          </div>
        )}
      </article>

      {/* Runtime Mode Selector Card */}
      <article className="card runtime-mode-card">
        <div className="card-header">
          <div>
            <h3>Modo de Ejecución del Sistema</h3>
            <p className="card-subtitle">
              Modo activo actual: <strong>{readiness?.active_mode || 'mock'}</strong>
            </p>
          </div>
          <span className="badge badge-accent">{readiness?.active_mode || 'mock'}</span>
        </div>

        <div className="mode-buttons-row">
          <button
            type="button"
            className={`button ${readiness?.active_mode === 'mock' ? 'button-accent' : 'button-secondary'}`}
            onClick={() => void handleSwitchMode('mock')}
            disabled={!isAdmin || busy || readiness?.active_mode === 'mock'}
          >
            Modo Mock (Desarrollo / Sintético)
          </button>
          <button
            type="button"
            className={`button ${readiness?.active_mode === 'preparation' ? 'button-accent' : 'button-secondary'}`}
            onClick={() => void handleSwitchMode('preparation')}
            disabled={!isAdmin || busy || readiness?.active_mode === 'preparation'}
          >
            Modo Preparación (Configuración segura)
          </button>
          <button
            type="button"
            className={`button ${readiness?.active_mode === 'real' ? 'button-accent' : 'button-primary'}`}
            onClick={() => void handleSwitchMode('real')}
            disabled={!isAdmin || busy || !canActivateReal || readiness?.active_mode === 'real'}
          >
            Modo Real (IA Gemma + OpenCV)
          </button>
        </div>

        {!canActivateReal && readiness?.preparation_reasons && readiness.preparation_reasons.length > 0 && (
          <div className="blockers-box">
            <h4>Requisitos pendientes para activar el Modo Real:</h4>
            <ul>
              {readiness.preparation_reasons.map((reason, idx) => (
                <li key={idx}>{reason}</li>
              ))}
            </ul>
          </div>
        )}
      </article>

      {/* Configuration Versions, Task Progress and Rollback */}
      <article className="card versions-card">
        <div className="card-header">
          <h3>Control de Versiones y Reversión (Rollback)</h3>
        </div>

        <div className="versions-info-grid">
          <div>
            <span className="status-label">Versión guardada</span>
            <strong>v{config?.saved_version ?? 1}</strong>
          </div>
          <div>
            <span className="status-label">Versión activa</span>
            <strong>v{config?.active_version ?? 1}</strong>
          </div>
          <div>
            <span className="status-label">Estado de cambios</span>
            <strong>{config?.is_dirty ? 'Cambios pendientes de aplicar' : 'Sincronizado'}</strong>
          </div>
        </div>

        {task && task.state === 'running' && (
          <div className="task-progress-box">
            <div className="progress-label-row">
              <span>Etapa: {task.stage}</span>
              <span>{Math.round(task.progress * 100)}%</span>
            </div>
            <div className="progress-bar-track">
              <div
                className="progress-bar-fill"
                style={{ width: `${Math.round(task.progress * 100)}%` }}
              />
            </div>
          </div>
        )}

        {isAdmin && (
          <div className="rollback-action-row">
            <button
              type="button"
              className="button button-warning button-sm"
              onClick={() => void handleRollback()}
              disabled={busy}
            >
              Restaurar versión anterior (Rollback)
            </button>
            <small className="field-hint">Restaura la última copia de respaldo conocida en caso de error.</small>
          </div>
        )}
      </article>

      {/* Portable Config Export & Import */}
      <article className="card export-import-card">
        <div className="card-header">
          <h3>Exportación e Importación Portable (JSON)</h3>
        </div>

        <div className="export-import-body">
          <p>
            Permite respaldar o transferir la configuración entre equipos sin incluir secretos ni rutas locales específicas.
          </p>
          <p className="warning-note">
            <em>Nota importante:</em> Las fotografías de referencia visuales no están incluidas en el archivo JSON exportado.
          </p>

          <div className="export-import-actions">
            <button
              type="button"
              className="button button-secondary"
              onClick={() => void handleExport()}
              disabled={!isAdmin}
            >
              ⬇ Exportar configuración (JSON)
            </button>

            {isAdmin && (
              <label className="button button-secondary import-label">
                <span>⬆ Seleccionar archivo para importar</span>
                <input
                  type="file"
                  accept="application/json,.json"
                  onChange={(e) => {
                    const file = e.target.files?.[0] || null
                    e.target.value = ''
                    handleSelectImportFile(file)
                  }}
                />
              </label>
            )}
          </div>

          {importPreview && (
            <div className="import-preview-box">
              <h4>Vista previa de importación: {importFileName}</h4>
              <p>
                El archivo contiene configuración estructurada lista para aplicar.
              </p>
              <div className="import-actions">
                <button
                  type="button"
                  className="button button-sm button-accent"
                  onClick={() => void handleConfirmImport()}
                  disabled={busy}
                >
                  {busy ? 'Importando…' : 'Confirmar e importar'}
                </button>
                <button
                  type="button"
                  className="button button-sm button-secondary"
                  onClick={() => {
                    setImportPreview(null)
                    setImportFileName(null)
                  }}
                >
                  Cancelar
                </button>
              </div>
            </div>
          )}
        </div>
      </article>
    </section>
  )
}
