import { useCallback, useEffect, useRef, useState } from 'react'
import {
  calibrateBackground,
  getConfig,
  getCalibration,
  getCalibrationBackgroundBlob,
  updateConfig,
  updateRoi,
  getCalibrationProfiles,
  captureProfileBackground,
  updateProfileRoi,
  confirmLegacyProfile,
  type Credentials,
} from '../api'
import type { CalibrationMeta, CalibrationProfile, ConfigResponse, SettingsConfig } from '../types'

interface CalibrationViewProps {
  apiUrl: string
  credentials: Credentials | null
  config: ConfigResponse | null
  draftConfig?: ConfigResponse | null
  onConfigUpdated: (config: ConfigResponse) => void
  onServerConfigRefreshed?: (config: ConfigResponse, acceptedSettings?: Array<keyof SettingsConfig>) => void
  onDraftChange?: (draft: ConfigResponse) => void
}

export function CalibrationView({
  apiUrl,
  credentials,
  config,
  draftConfig,
  onConfigUpdated,
  onServerConfigRefreshed,
  onDraftChange,
}: CalibrationViewProps) {
  const isAdmin = credentials?.role === 'admin'
  const initialSettings = draftConfig?.settings || config?.settings || null
  const [settings, setSettings] = useState<SettingsConfig | null>(() => initialSettings)
  const [calibration, setCalibration] = useState<CalibrationMeta | null>(null)
  const [profiles, setProfiles] = useState<Record<string, CalibrationProfile>>({})
  const [selectedProfileSource, setSelectedProfileSource] = useState<string>('remote_phone')
  const [confirmingLegacy, setConfirmingLegacy] = useState(false)
  const [backgroundUrl, setBackgroundUrl] = useState<string | null>(null)
  const [roi, setRoi] = useState<[number, number, number, number] | null>(() => initialSettings?.roi || null)
  const [drawing, setDrawing] = useState(false)
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null)
  const [drawCurrent, setDrawCurrent] = useState<{ x: number; y: number } | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [savingRoi, setSavingRoi] = useState(false)
  const [uploadingBg, setUploadingBg] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const bgBlobUrlRef = useRef<string | null>(null)

  // Notify parent of settings changes while preserving the canonical draft
  // inventory (which may contain unsaved tool edits from another section).
  useEffect(() => {
    const source = draftConfig || config
    if (source && settings && onDraftChange) {
      const next = {
        ...source,
        settings,
      }
      if (JSON.stringify(next.settings) !== JSON.stringify(source.settings)) onDraftChange(next)
    }
  }, [onDraftChange, settings])

  // Keep settings in sync with external config if not dirty
  useEffect(() => {
    const source = draftConfig || config
    if (source?.settings) {
      if (JSON.stringify(settings) !== JSON.stringify(source.settings)) setSettings(source.settings)
      if (source.settings.roi && JSON.stringify(roi) !== JSON.stringify(source.settings.roi)) {
        setRoi(source.settings.roi)
      }
    }
  }, [config, draftConfig])

  // Cleanup object URLs on unmount
  useEffect(() => {
    return () => {
      if (bgBlobUrlRef.current) {
        URL.revokeObjectURL(bgBlobUrlRef.current)
      }
    }
  }, [])

  // Load calibration metadata and background blob
  const loadCalibration = useCallback(async () => {
    if (!credentials) return
    try {
      const meta = await getCalibration(apiUrl, credentials)
      setCalibration(meta)
      if (meta.roi) setRoi(meta.roi)

      if (meta.calibrated) {
        const blob = await getCalibrationBackgroundBlob(apiUrl, credentials)
        if (bgBlobUrlRef.current) URL.revokeObjectURL(bgBlobUrlRef.current)
        const url = URL.createObjectURL(blob)
        bgBlobUrlRef.current = url
        setBackgroundUrl(url)
      } else {
        setBackgroundUrl(null)
      }
    } catch (caught) {
      // Background might not exist yet on fresh install, that's expected
      setCalibration({ calibrated: false, status: 'missing', image: null, roi: null })
      setBackgroundUrl(null)
    }
  }, [apiUrl, credentials])

  const refreshAuthoritativeConfig = useCallback(async (acceptedSettings: Array<keyof SettingsConfig> = []) => {
    if (!credentials || !onServerConfigRefreshed) return null
    try {
      const latest = await getConfig(apiUrl, credentials)
      onServerConfigRefreshed(latest, acceptedSettings)
      return latest
    } catch {
      return null
    }
  }, [apiUrl, credentials, onServerConfigRefreshed])

  useEffect(() => {
    void loadCalibration()
  }, [loadCalibration])

  const loadProfiles = useCallback(async () => {
    if (!credentials) return
    try {
      const res = await getCalibrationProfiles(apiUrl, credentials)
      if (res && res.profiles) {
        setProfiles(res.profiles)
      }
    } catch {
      // ignore
    }
  }, [apiUrl, credentials])

  useEffect(() => {
    void loadProfiles()
  }, [loadProfiles])

  const handleConfirmLegacy = async (profileId: string) => {
    if (!credentials) return
    setConfirmingLegacy(true)
    setError(null)
    setSuccessMessage(null)
    try {
      await confirmLegacyProfile(apiUrl, credentials, profileId)
      await loadProfiles()
      setSuccessMessage(`Perfil heredado ${profileId} confirmado para producción.`)
    } catch (caught: any) {
      setError(caught instanceof Error ? caught.message : 'Error al confirmar perfil')
    } finally {
      setConfirmingLegacy(false)
    }
  }

  const handleCaptureSourceBackground = async (sourceId: string, file?: File | null) => {
    if (!credentials) return
    setUploadingBg(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const updated = await captureProfileBackground(apiUrl, credentials, sourceId, file || undefined)
      setProfiles((previous) => ({ ...previous, [sourceId]: updated }))
      if (updated.resolution?.width && updated.resolution?.height) {
        setCalibration((previous) => previous || {
          calibrated: false,
          status: updated.status,
          image: { width: updated.resolution.width, height: updated.resolution.height, orientation: updated.orientation },
          roi: updated.roi || null,
        })
      }
      await loadProfiles()
      setSuccessMessage(`Fondo guardado con éxito para ${sourceId} (${updated.resolution.width}x${updated.resolution.height}).`)
    } catch (caught: any) {
      setError(caught instanceof Error ? caught.message : 'Error al capturar fondo para esta cámara')
    } finally {
      setUploadingBg(false)
    }
  }

  // Recognition mode handlers
  const handleToggleHybrid = async (enabled: boolean) => {
    if (!settings || !credentials || !config) return
    const nextMode = enabled ? 'hybrid' : 'text-only'
    const nextSettings: SettingsConfig = { ...settings, gallery_mode: nextMode }
    setSettings(nextSettings)
    await saveSettingsDraft(nextSettings)
  }

  const handleModeChange = async (mode: 'text-only' | 'hybrid' | 'image-only') => {
    if (!settings || !credentials || !config) return
    const nextSettings: SettingsConfig = { ...settings, gallery_mode: mode }
    setSettings(nextSettings)
    await saveSettingsDraft(nextSettings)
  }

  const handleTextWeightChange = async (weight: number) => {
    if (!settings || !credentials || !config) return
    const nextSettings: SettingsConfig = { ...settings, text_weight: weight }
    setSettings(nextSettings)
  }

  const handleSaveParameters = async () => {
    if (!settings) return
    await saveSettingsDraft(settings)
  }

  const saveSettingsDraft = async (updatedSettings: SettingsConfig) => {
    if (!credentials || !config) return
    const source = draftConfig || config
    setSavingSettings(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const payload = {
        expected_version: source.saved_version,
        settings: updatedSettings,
        inventory: source.inventory,
      }
      const res = await updateConfig(apiUrl, credentials, payload)
      onConfigUpdated(res)
      setSuccessMessage('Parámetros de reconocimiento guardados en el borrador.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al guardar parámetros')
    } finally {
      setSavingSettings(false)
    }
  }

  // Background upload handler
  const handleUploadBackground = async (file: File | null) => {
    if (!file || !credentials) return
    setUploadingBg(true)
    setError(null)
    setSuccessMessage(null)
    try {
      await calibrateBackground(apiUrl, credentials, file)
      await refreshAuthoritativeConfig()
      setSuccessMessage('Fotografía de fondo de bandeja vacía guardada correctamente.')
      await loadCalibration()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al guardar foto de fondo')
    } finally {
      setUploadingBg(false)
    }
  }

  // Interactive ROI drawing on SVG
  const getSvgCoordinates = (e: React.PointerEvent<SVGSVGElement>): { x: number; y: number } | null => {
    const svg = svgRef.current
    if (!svg) return null
    const imgW = calibration?.image?.width || 1280
    const imgH = calibration?.image?.height || 960
    if (typeof svg.createSVGPoint !== 'function') {
      const rect = svg.getBoundingClientRect()
      const scaleX = rect.width ? imgW / rect.width : 1
      const scaleY = rect.height ? imgH / rect.height : 1
      return {
        x: Math.max(0, Math.min(imgW, Math.round((e.clientX - rect.left) * scaleX))),
        y: Math.max(0, Math.min(imgH, Math.round((e.clientY - rect.top) * scaleY))),
      }
    }
    const pt = svg.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const svgP = pt.matrixTransform(svg.getScreenCTM()?.inverse())
    return {
      x: Math.max(0, Math.min(imgW, Math.round(svgP.x))),
      y: Math.max(0, Math.min(imgH, Math.round(svgP.y))),
    }
  }

  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!isAdmin || !backgroundUrl) return
    const coords = getSvgCoordinates(e)
    if (!coords) return
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
    setDrawing(true)
    setDrawStart(coords)
    setDrawCurrent(coords)
  }

  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drawing || !drawStart) return
    const coords = getSvgCoordinates(e)
    if (coords) setDrawCurrent(coords)
  }

  const handlePointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drawing || !drawStart || !drawCurrent) return
    setDrawing(false)
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }

    const x = Math.min(drawStart.x, drawCurrent.x)
    const y = Math.min(drawStart.y, drawCurrent.y)
    const w = Math.abs(drawCurrent.x - drawStart.x)
    const h = Math.abs(drawCurrent.y - drawStart.y)

    // Enforce minimum ROI size (at least 20x20 pixels) and bounds clamp
    const imgW = calibration?.image?.width || 1280
    const imgH = calibration?.image?.height || 960
    const clampedX = Math.max(0, Math.min(imgW - 20, x))
    const clampedY = Math.max(0, Math.min(imgH - 20, y))
    const clampedW = Math.min(imgW - clampedX, Math.max(20, w))
    const clampedH = Math.min(imgH - clampedY, Math.max(20, h))

    setRoi([clampedX, clampedY, clampedW, clampedH])
    setDrawStart(null)
    setDrawCurrent(null)
  }

  const handlePointerCancel = (e: React.PointerEvent<SVGSVGElement>) => {
    setDrawing(false)
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* ignore */
    }
    setDrawStart(null)
    setDrawCurrent(null)
  }

  // Save ROI coordinates
  const handleSaveRoi = async () => {
    if (!credentials || !roi) return
    setSavingRoi(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const selectedProfile = profiles[selectedProfileSource]
      const profileResult = selectedProfile
        ? await updateProfileRoi(apiUrl, credentials, selectedProfileSource, roi)
        : null
      const res = profileResult || await updateRoi(apiUrl, credentials, roi)
      const savedRoi = res.roi || roi
      setRoi(savedRoi)
      if (profileResult) {
        setProfiles((previous) => ({ ...previous, [selectedProfileSource]: profileResult }))
      }
      const latest = profileResult ? null : await refreshAuthoritativeConfig(['roi'])
      const source = draftConfig || config
      if (!latest && source && onDraftChange) {
        onDraftChange({
          ...source,
          settings: { ...source.settings, roi: savedRoi },
        })
      }
      setSuccessMessage(`Región de interés (ROI [${savedRoi.join(', ')}]) guardada correctamente.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al guardar ROI')
    } finally {
      setSavingRoi(false)
    }
  }

  const handleResetRoiFull = () => {
    if (!calibration?.image) return
    setRoi([0, 0, calibration.image.width, calibration.image.height])
  }

  // Dimensions for SVG rendering
  const bgWidth = calibration?.image?.width || 1280
  const bgHeight = calibration?.image?.height || 960

  // Calculate in-progress drawing rectangle
  const currentDrawRect = drawing && drawStart && drawCurrent
    ? {
        x: Math.min(drawStart.x, drawCurrent.x),
        y: Math.min(drawStart.y, drawCurrent.y),
        w: Math.abs(drawCurrent.x - drawStart.x),
        h: Math.abs(drawCurrent.y - drawStart.y),
      }
    : null

  const isHybrid = settings?.gallery_mode === 'hybrid'

  return (
    <section className="calibration-view" aria-label="Calibración y Modos de Reconocimiento">
      <header className="section-header">
        <div>
          <p className="eyebrow">Calibración</p>
          <h2>Reconocimiento IA y Bandeja Vacía</h2>
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
          <strong>Modo de solo lectura:</strong> Se requiere rol Administrador para cambiar modos de reconocimiento o calibrar la bandeja.
        </div>
      )}
      {/* Section 1: Recognition Modes */}
      <article className="card calibration-mode-card">
        <div className="card-header">
          <h3>Modo de Reconocimiento Zero-Shot e Híbrido</h3>
          <span className="badge badge-accent">
            {settings?.gallery_mode === 'hybrid'
              ? 'Híbrido (Texto + Fotos)'
              : settings?.gallery_mode === 'image-only'
              ? 'Solo Imágenes'
              : 'Solo Texto (Predeterminado)'}
          </span>
        </div>

        <div className="hybrid-toggle-container">
          <label className="switch-label">
            <span className="switch-title">Mejorar con fotografías</span>
            <span className="switch-desc">
              Combina las descripciones textuales con las fotos de referencia disponibles para cada herramienta.
            </span>
            <div className="switch-wrapper">
              <input
                type="checkbox"
                checked={isHybrid}
                onChange={(e) => void handleToggleHybrid(e.target.checked)}
                disabled={!isAdmin || savingSettings}
                aria-label="Interruptor de modo híbrido"
              />
              <span className="switch-slider" />
            </div>
          </label>
        </div>

        <p className="mode-evaluation-note">
          <strong>Nota de evaluación:</strong> El modo híbrido utiliza prototipos ponderados para las herramientas que tengan fotos y solo texto para las restantes. Debe evaluarse con pruebas reales de campo; no se garantiza que añada mayor precisión automáticamente.
        </p>

        {/* Advanced Options Accordion */}
        <div className="advanced-options-group">
          <button
            type="button"
            className="disclosure-btn"
            onClick={() => setAdvancedOpen(!advancedOpen)}
            aria-expanded={advancedOpen}
          >
            <span>{advancedOpen ? '⌃ Ocultar opciones avanzadas' : '⌄ Configuración avanzada de clasificación'}</span>
          </button>

          {advancedOpen && (
            <div className="advanced-options-body">
              <div className="radio-group" role="radiogroup" aria-label="Modalidad de galería">
                <label className="radio-option">
                  <input
                    type="radio"
                    name="gallery_mode"
                    value="text-only"
                    checked={settings?.gallery_mode === 'text-only'}
                    onChange={() => void handleModeChange('text-only')}
                    disabled={!isAdmin}
                  />
                  <span><strong>Solo texto:</strong> Usa nombres y descripciones del inventario (zero-shot).</span>
                </label>
                <label className="radio-option">
                  <input
                    type="radio"
                    name="gallery_mode"
                    value="hybrid"
                    checked={settings?.gallery_mode === 'hybrid'}
                    onChange={() => void handleModeChange('hybrid')}
                    disabled={!isAdmin}
                  />
                  <span><strong>Texto + imágenes (híbrido):</strong> Combina descripciones con fotos opcionales.</span>
                </label>
                <label className="radio-option">
                  <input
                    type="radio"
                    name="gallery_mode"
                    value="image-only"
                    checked={settings?.gallery_mode === 'image-only'}
                    onChange={() => void handleModeChange('image-only')}
                    disabled={!isAdmin}
                  />
                  <span><strong>Solo imágenes:</strong> Requiere fotos de referencia en las 9 herramientas.</span>
                </label>
              </div>

              {isHybrid && (
                <div className="weight-slider-group">
                  <div className="slider-header">
                    <label htmlFor="text-weight-slider">Ponderación Texto vs Imágenes</label>
                    <span className="slider-value">
                      Texto: {Math.round((settings?.text_weight ?? 0.5) * 100)}% / Fotos: {Math.round((1 - (settings?.text_weight ?? 0.5)) * 100)}%
                    </span>
                  </div>
                  <input
                    id="text-weight-slider"
                    type="range"
                    min="0.10"
                    max="0.90"
                    step="0.05"
                    value={settings?.text_weight ?? 0.5}
                    onChange={(e) => void handleTextWeightChange(parseFloat(e.target.value))}
                    disabled={!isAdmin}
                  />
                </div>
              )}

              <div className="thresholds-row">
                <label>
                  Umbral mínimo de similitud coseno
                  <input
                    type="number"
                    step="0.01"
                    min="0.30"
                    max="0.95"
                    value={settings?.classifier_threshold ?? 0.70}
                    onChange={(e) => setSettings(settings ? { ...settings, classifier_threshold: parseFloat(e.target.value) } : null)}
                    disabled={!isAdmin}
                  />
                  <small className="field-hint">Valores menores aceptan más objetos; mayores rechazan ambigüedades.</small>
                </label>
                <label>
                  Margen mínimo entre 1ª y 2ª opción
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    max="0.30"
                    value={settings?.classifier_margin ?? 0.08}
                    onChange={(e) => setSettings(settings ? { ...settings, classifier_margin: parseFloat(e.target.value) } : null)}
                    disabled={!isAdmin}
                  />
                  <small className="field-hint">Diferencia mínima de similitud para confirmar una detección.</small>
                </label>
              </div>

              {isAdmin && (
                <div className="advanced-actions">
                  <button
                    type="button"
                    className="button button-sm button-secondary"
                    onClick={() => void handleSaveParameters()}
                    disabled={savingSettings}
                  >
                    {savingSettings ? 'Guardando…' : 'Guardar parámetros avanzados'}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </article>

      {/* Section 2: Tray Background Calibration and ROI */}
      <article className="card calibration-tray-card">
        <div className="card-header">
          <h3>Calibración de la Bandeja Vacía y Región de Interés (ROI)</h3>
          <span className={`badge ${calibration?.calibrated ? 'badge-ok' : 'badge-warning'}`}>
            {calibration?.calibrated ? 'Fondo calibrado' : 'Faltante'}
          </span>
        </div>

        <div className="tray-instructions">
          <h4>Recomendaciones clave de calibración:</h4>
          <ul>
            <li><strong>Cámara fija:</strong> Ubica el teléfono en soporte cenital fijo, perpendicular al plano de la bandeja.</li>
            <li><strong>Bandeja totalmente vacía:</strong> Retira todas las herramientas antes de capturar el fondo.</li>
            <li><strong>Iluminación:</strong> Evita reflejos intensos o sombras de personas sobre la bandeja.</li>
            <li><strong>Recalibración:</strong> Si el teléfono se mueve o cambia de posición, repite la captura del fondo y ajusta el ROI.</li>
          </ul>
        </div>

        {/* Upload Empty Tray Background */}
        <div className="tray-capture-actions">
          <label className="button button-primary">
            <span>📷 Tomar foto de bandeja vacía (Cámara)</span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              onChange={(e) => {
                const file = e.target.files?.[0] || null
                e.target.value = ''
                void handleUploadBackground(file)
              }}
              disabled={!isAdmin || uploadingBg}
            />
          </label>
          <label className="button button-secondary">
            <span>📁 Seleccionar archivo de fondo</span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => {
                const file = e.target.files?.[0] || null
                e.target.value = ''
                void handleUploadBackground(file)
              }}
              disabled={!isAdmin || uploadingBg}
            />
          </label>
          {uploadingBg && <span className="loading-copy">Subiendo y analizando fondo…</span>}
        </div>

        {/* Interactive Background & ROI Viewer */}
        <div className="roi-viewer-container">
          <div className="roi-viewer-header">
            <h4>Vista del fondo y Región de Interés</h4>
            {calibration?.image && (
              <span className="dimension-badge">
                {calibration.image.width} × {calibration.image.height} px
              </span>
            )}
          </div>

          {backgroundUrl ? (
            <div className="roi-canvas-wrapper" style={{ touchAction: 'none' }}>
              <svg
                ref={svgRef}
                viewBox={`0 0 ${bgWidth} ${bgHeight}`}
                className="roi-svg"
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                onPointerCancel={handlePointerCancel}
                aria-label="Fondo de calibración interactivo para dibujar ROI"
              >
                <image href={backgroundUrl} x="0" y="0" width={bgWidth} height={bgHeight} />

                {/* Confirmed / Saved ROI Rectangle */}
                {roi && !drawing && (
                  <g>
                    <rect
                      x={roi[0]}
                      y={roi[1]}
                      width={roi[2]}
                      height={roi[3]}
                      className="roi-rect"
                    />
                    <text x={roi[0] + 12} y={roi[1] + 28} className="roi-text">
                      ROI: {roi[2]}×{roi[3]} px
                    </text>
                  </g>
                )}

                {/* Live drawing preview rectangle */}
                {currentDrawRect && (
                  <rect
                    x={currentDrawRect.x}
                    y={currentDrawRect.y}
                    width={currentDrawRect.w}
                    height={currentDrawRect.h}
                    className="roi-rect-drawing"
                  />
                )}
              </svg>
              <p className="roi-hint">
                {isAdmin
                  ? 'Arrastra sobre la imagen para definir el rectángulo del área de trabajo (ROI).'
                  : 'Fondo calibrado visible. Requiere rol Administrador para redibujar el ROI.'}
              </p>
            </div>
          ) : (
            <div className="empty-calibration-box">
              <span className="crosshair" aria-hidden="true">＋</span>
              <strong>No hay fotografía de fondo guardada</strong>
              <p>Toma una foto de la bandeja vacía desde el teléfono para calibrar la segmentación de objetos.</p>
            </div>
          )}

          {/* Numeric ROI controls & save */}
          <div className="numeric-roi-row">
            <div className="numeric-fields">
              <label>
                X
                <input
                  type="number"
                  value={roi ? roi[0] : 0}
                  onChange={(e) => setRoi([parseInt(e.target.value, 10) || 0, roi ? roi[1] : 0, roi ? roi[2] : bgWidth, roi ? roi[3] : bgHeight])}
                  disabled={!isAdmin}
                />
              </label>
              <label>
                Y
                <input
                  type="number"
                  value={roi ? roi[1] : 0}
                  onChange={(e) => setRoi([roi ? roi[0] : 0, parseInt(e.target.value, 10) || 0, roi ? roi[2] : bgWidth, roi ? roi[3] : bgHeight])}
                  disabled={!isAdmin}
                />
              </label>
              <label>
                Ancho
                <input
                  type="number"
                  value={roi ? roi[2] : bgWidth}
                  onChange={(e) => setRoi([roi ? roi[0] : 0, roi ? roi[1] : 0, parseInt(e.target.value, 10) || 10, roi ? roi[3] : bgHeight])}
                  disabled={!isAdmin}
                />
              </label>
              <label>
                Alto
                <input
                  type="number"
                  value={roi ? roi[3] : bgHeight}
                  onChange={(e) => setRoi([roi ? roi[0] : 0, roi ? roi[1] : 0, roi ? roi[2] : bgWidth, parseInt(e.target.value, 10) || 10])}
                  disabled={!isAdmin}
                />
              </label>
            </div>

            {isAdmin && (
              <div className="roi-actions">
                <button
                  type="button"
                  className="button button-sm button-secondary"
                  onClick={handleResetRoiFull}
                  disabled={savingRoi}
                >
                  Restablecer a bandeja completa
                </button>
                <button
                  type="button"
                  className="button button-sm button-accent"
                  onClick={() => void handleSaveRoi()}
                  disabled={!roi || savingRoi}
                >
                  {savingRoi ? 'Guardando…' : 'Guardar ROI'}
                </button>
              </div>
            )}
          </div>
        </div>
      </article>

      {/* Section 3: Multi-Camera Calibration Profiles */}
      <article className="card calibration-mode-card" aria-label="Perfiles de Calibración por Cámara">
        <div className="card-header">
          <h3>Perfiles de Calibración por Cámara (Multicámara)</h3>
          <span className="badge badge-accent">
            {Object.keys(profiles).length} perfiles
          </span>
        </div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', margin: '14px 0' }}>
          {['remote_phone', 'laptop_builtin', 'usb_webcam', 'operator_device', 'manual_upload', 'unbound_legacy'].map((srcId) => {
            const prof = profiles[srcId]
            const isSelected = selectedProfileSource === srcId
            const statusColor = prof?.status === 'ready' ? '#166534' : prof?.status === 'unbound_legacy' ? '#c2410c' : '#854d0e'
            const statusBg = prof?.status === 'ready' ? '#dcfce7' : prof?.status === 'unbound_legacy' ? '#ffedd5' : '#fef9c3'
            return (
              <button
                key={srcId}
                type="button"
                className={`button button-sm ${isSelected ? 'button-primary' : 'button-secondary'}`}
                onClick={() => setSelectedProfileSource(srcId)}
              >
                <span>{srcId}</span>
                {prof && (
                  <span style={{ fontSize: '10px', padding: '2px 6px', borderRadius: '4px', background: statusBg, color: statusColor, fontWeight: 700 }}>
                    {prof.status}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {profiles[selectedProfileSource] ? (
          <div style={{ padding: '14px', border: '1px solid #e2e8f0', borderRadius: '10px', background: '#f8fafc' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <strong>Fuente seleccionada: {selectedProfileSource}</strong>
              <span className={`badge ${profiles[selectedProfileSource].status === 'ready' ? 'badge-accent' : 'badge-neutral'}`}>
                {profiles[selectedProfileSource].status}
              </span>
            </div>
            <p style={{ margin: '4px 0', fontSize: '13px', color: '#475569' }}>
              Resolución: {profiles[selectedProfileSource].resolution.width}x{profiles[selectedProfileSource].resolution.height} · Orientación: {profiles[selectedProfileSource].orientation}
            </p>
            {profiles[selectedProfileSource].background_filename && (
              <p style={{ margin: '4px 0', fontSize: '13px', color: '#475569' }}>
                Archivo de fondo: <code>{profiles[selectedProfileSource].background_filename}</code>
              </p>
            )}
            {profiles[selectedProfileSource].roi && (
              <p style={{ margin: '4px 0', fontSize: '13px', color: '#475569' }}>
                ROI: [{profiles[selectedProfileSource].roi.join(', ')}]
              </p>
            )}

            {profiles[selectedProfileSource].needs_confirmation && isAdmin && (
              <div style={{ marginTop: '12px' }}>
                <button
                  type="button"
                  className="button button-accent button-sm"
                  onClick={() => void handleConfirmLegacy(selectedProfileSource)}
                  disabled={confirmingLegacy}
                >
                  {confirmingLegacy ? 'Confirmando...' : 'Confirmar Perfil Heredado para Producción'}
                </button>
              </div>
            )}

            {isAdmin && (
              <div style={{ marginTop: '12px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <label className="button button-sm button-secondary">
                  <span>Subir foto de fondo</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png"
                    style={{ position: 'absolute', width: '1px', height: '1px', clip: 'rect(0,0,0,0)' }}
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      e.target.value = ''
                      void handleCaptureSourceBackground(selectedProfileSource, f)
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="button button-sm button-secondary"
                  onClick={() => void handleCaptureSourceBackground(selectedProfileSource)}
                  disabled={uploadingBg}
                >
                  {uploadingBg ? 'Capturando...' : 'Capturar desde fuente activa'}
                </button>
              </div>
            )}
          </div>
        ) : (
          <div style={{ padding: '14px', color: '#64748b', fontSize: '13px', background: '#f8fafc', borderRadius: '10px' }}>
            No se ha calibrado esta fuente todavía. Captura o sube una imagen de fondo para crear su perfil.
            {isAdmin && (
              <div style={{ marginTop: '10px' }}>
                <label className="button button-sm button-primary">
                  <span>Capturar Fondo Inicial</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png"
                    style={{ position: 'absolute', width: '1px', height: '1px', clip: 'rect(0,0,0,0)' }}
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      e.target.value = ''
                      void handleCaptureSourceBackground(selectedProfileSource, f)
                    }}
                  />
                </label>
              </div>
            )}
          </div>
        )}
      </article>
    </section>
  )
}
