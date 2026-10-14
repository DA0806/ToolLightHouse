import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  applyConfig,
  deleteReference,
  getConfig,
  listReferences,
  updateConfig,
  uploadReference,
  type Credentials,
} from '../api'
import type { ConfigResponse, ReferencePhoto, ToolCategoryItem } from '../types'

interface ToolManagerProps {
  apiUrl: string
  credentials: Credentials | null
  config: ConfigResponse | null
  draftConfig?: ConfigResponse | null
  onConfigUpdated: (config: ConfigResponse) => void
  onServerConfigRefreshed?: (config: ConfigResponse) => void
  onDraftChange?: (draft: ConfigResponse) => void
  onNavigateTab?: (tab: 'inspection' | 'calibration' | 'arduino' | 'system') => void
}

function sanitizeSemanticName(val: string): string {
  const sanitized = val
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return sanitized || 'herramienta'
}

export function ToolManager({
  apiUrl,
  credentials,
  config,
  draftConfig,
  onConfigUpdated,
  onServerConfigRefreshed,
  onDraftChange,
}: ToolManagerProps) {
  const isAdmin = credentials?.role === 'admin'
  const initialSource = draftConfig || config
  const [categories, setCategories] = useState<ToolCategoryItem[]>(() => initialSource?.inventory?.categories || [])
  const [inventoryExample, setInventoryExample] = useState<boolean>(() => Boolean(initialSource?.inventory?.example))
  const [inventoryConfirmed, setInventoryConfirmed] = useState<boolean>(() => Boolean(initialSource?.settings?.inventory_confirmed))
  const [focusedClassId, setFocusedClassId] = useState<string | null>(null)
  const [newDescInput, setNewDescInput] = useState<Record<string, string>>({})
  const [photoOpenClassId, setPhotoOpenClassId] = useState<string | null>(null)
  const [references, setReferences] = useState<Record<string, ReferencePhoto[]>>({})
  const [loadingRefs, setLoadingRefs] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [conflictOpen, setConflictOpen] = useState(false)
  const cardRefs = useRef<Record<string, HTMLElement | null>>({})

  // Keep local draft in sync if external config changes and user hasn't typed
  const isDraftDirty = useMemo(() => {
    if (!config?.inventory?.categories) return false
    const serverCategories = config.inventory.categories
    if (categories.length !== serverCategories.length) return true
    for (let i = 0; i < categories.length; i++) {
      const c = categories[i]
      const s = serverCategories[i]
      if (!s) return true
      if (
        c.class_id !== s.class_id ||
        c.name !== s.name ||
        (c.display_name ?? null) !== (s.display_name ?? null) ||
        c.location !== s.location ||
        c.led_index !== s.led_index ||
        c.descriptions.length !== s.descriptions.length ||
        c.descriptions.some((d, idx) => d !== s.descriptions[idx])
      ) {
        return true
      }
    }
    return (
      inventoryExample !== Boolean(config.inventory.example) ||
      inventoryConfirmed !== Boolean(config.settings.inventory_confirmed)
    )
  }, [categories, config, inventoryConfirmed, inventoryExample])

  // Sync only this editor's owned fields into the canonical parent draft.
  // The source is draftConfig, never the potentially newer server snapshot.
  useEffect(() => {
    const source = draftConfig || config
    if (source && onDraftChange) {
      const next = {
        ...source,
        settings: {
          ...source.settings,
          inventory_confirmed: inventoryConfirmed,
        },
        inventory: {
          example: inventoryExample,
          categories,
        },
      }
      if (
        JSON.stringify(next.settings) !== JSON.stringify(source.settings) ||
        JSON.stringify(next.inventory) !== JSON.stringify(source.inventory)
      ) onDraftChange(next)
    }
  }, [categories, inventoryConfirmed, inventoryExample, onDraftChange])

  // Initial load or external config updates when clean
  useEffect(() => {
    const source = draftConfig || config
    if (source?.inventory?.categories) {
      if (JSON.stringify(categories) !== JSON.stringify(source.inventory.categories)) setCategories(source.inventory.categories)
      if (inventoryExample !== Boolean(source.inventory.example)) setInventoryExample(Boolean(source.inventory.example))
      if (inventoryConfirmed !== Boolean(source.settings.inventory_confirmed)) setInventoryConfirmed(Boolean(source.settings.inventory_confirmed))
    }
  }, [config, draftConfig])

  // LED Swap Logic: Atomically swap LED assignments between tools to prevent duplicates
  const handleLedChange = (classId: string, newLedIndex: number) => {
    setCategories((prev) => {
      const current = prev.find((c) => c.class_id === classId)
      if (!current || current.led_index === newLedIndex) return prev
      const oldLedIndex = current.led_index

      // Find the other tool that has newLedIndex
      return prev.map((cat) => {
        if (cat.class_id === classId) {
          return { ...cat, led_index: newLedIndex, pin: newLedIndex + 2 }
        }
        if (cat.led_index === newLedIndex) {
          return { ...cat, led_index: oldLedIndex, pin: oldLedIndex + 2 }
        }
        return cat
      })
    })
  }

  // Update visible name; if name is still generic, update name too
  const handleDisplayNameChange = (classId: string, newDisplayName: string) => {
    setCategories((prev) =>
      prev.map((cat) => {
        if (cat.class_id !== classId) return cat
        const isGenericName = /^tool[_\s]*\d+$/i.test(cat.name)
        const updatedName = isGenericName ? sanitizeSemanticName(newDisplayName) : cat.name
        return {
          ...cat,
          display_name: newDisplayName,
          name: updatedName,
        }
      })
    )
  }

  // Explicit semantic name edit for IA
  const handleSemanticNameChange = (classId: string, newName: string) => {
    setCategories((prev) =>
      prev.map((cat) => (cat.class_id === classId ? { ...cat, name: sanitizeSemanticName(newName) } : cat))
    )
  }

  const handleLocationChange = (classId: string, newLocation: string) => {
    setCategories((prev) =>
      prev.map((cat) => (cat.class_id === classId ? { ...cat, location: newLocation } : cat))
    )
  }

  const handleAddDescription = (classId: string) => {
    const text = (newDescInput[classId] || '').trim()
    if (!text) return
    setCategories((prev) =>
      prev.map((cat) => {
        if (cat.class_id !== classId) return cat
        return { ...cat, descriptions: [...cat.descriptions, text] }
      })
    )
    setNewDescInput((prev) => ({ ...prev, [classId]: '' }))
  }

  const handleRemoveDescription = (classId: string, descIndex: number) => {
    setCategories((prev) =>
      prev.map((cat) => {
        if (cat.class_id !== classId) return cat
        return {
          ...cat,
          descriptions: cat.descriptions.filter((_, idx) => idx !== descIndex),
        }
      })
    )
  }

  // Focus and scroll to tool card from physical grid
  const scrollToCard = (classId: string) => {
    setFocusedClassId(classId)
    const element = cardRefs.current[classId]
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  // Confirm actual physical inventory
  const handleConfirmInventory = () => {
    setInventoryConfirmed(true)
    setInventoryExample(false)
    // Also ensure initial generic classification names are replaced if user set display_name
    setCategories((prev) =>
      prev.map((cat) => {
        if (/^tool[_\s]*\d+$/i.test(cat.name) && cat.display_name?.trim()) {
          return { ...cat, name: sanitizeSemanticName(cat.display_name) }
        }
        return cat
      })
    )
    setSuccessMessage('Inventario marcado como confirmado. Recuerda guardar y aplicar para persistir.')
  }

  // Load photos for category
  const togglePhotos = async (classId: string) => {
    if (photoOpenClassId === classId) {
      setPhotoOpenClassId(null)
      return
    }
    setPhotoOpenClassId(classId)
    if (!credentials) return
    setLoadingRefs(classId)
    try {
      const res = await listReferences(apiUrl, credentials, classId)
      setReferences((prev) => ({ ...prev, [classId]: res.references }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al listar fotos de referencia')
    } finally {
      setLoadingRefs(null)
    }
  }

  // Upload photos sequentially
  const handleUploadPhotos = async (classId: string, files: File[]) => {
    if (files.length === 0 || !credentials) return
    setError(null)
    const total = files.length
    let succeeded = 0
    const newPhotos: ReferencePhoto[] = []

    for (let i = 0; i < total; i++) {
      const file = files[i]
      setUploadProgress(`Subiendo foto ${i + 1} de ${total}…`)
      try {
        const photo = await uploadReference(apiUrl, credentials, classId, file)
        newPhotos.push(photo)
        succeeded++
      } catch (caught) {
        setError(
          caught instanceof Error
            ? `Error en foto ${file.name}: ${caught.message}`
            : `Error al subir ${file.name}`
        )
      }
    }

    setUploadProgress(null)
    if (newPhotos.length > 0) {
      setReferences((prev) => ({
        ...prev,
        [classId]: [...(prev[classId] || []), ...newPhotos],
      }))
      // Update reference count in categories list
      setCategories((prev) =>
        prev.map((c) =>
          c.class_id === classId
            ? { ...c, reference_count: (c.reference_count || 0) + succeeded }
            : c
        )
      )
      // Refresh config metadata in background to get new saved_version without wiping local form
      try {
        const latest = await getConfig(apiUrl, credentials)
        onServerConfigRefreshed?.(latest)
      } catch {
        /* ignore refresh error */
      }
      setSuccessMessage(`${succeeded} foto(s) subida(s) correctamente.`)
    }
  }

  // Delete photo
  const handleDeletePhoto = async (classId: string, imageId: string) => {
    if (!credentials) return
    if (!window.confirm('¿Seguro que deseas eliminar esta fotografía de referencia?')) return
    setError(null)
    try {
      await deleteReference(apiUrl, credentials, classId, imageId)
      const nextCats = categories.map((c) =>
        c.class_id === classId
          ? { ...c, reference_count: Math.max(0, (c.reference_count || 1) - 1) }
          : c
      )
      setReferences((prev) => ({
        ...prev,
        [classId]: (prev[classId] || []).filter((p) => p.image_id !== imageId),
      }))
      setCategories(nextCats)
      const latest = await getConfig(apiUrl, credentials)
      onServerConfigRefreshed?.(latest)
      setSuccessMessage('Fotografía eliminada.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo eliminar la foto')
    }
  }

  // Save draft configuration
  const handleSave = async (): Promise<ConfigResponse | null> => {
    if (!credentials || !config) return null
    const source = draftConfig || config
    setSaving(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const payload = {
        expected_version: source.saved_version,
        settings: {
          ...source.settings,
          inventory_confirmed: inventoryConfirmed,
        },
        inventory: {
          example: inventoryExample,
          categories: categories.map((c) => ({
            ...c,
            display_name: c.display_name?.trim() || null,
          })),
        },
      }
      const updated = await updateConfig(apiUrl, credentials, payload)
      onConfigUpdated(updated)
      setSuccessMessage(`Configuración guardada (v${updated.saved_version}).`)
      return updated
    } catch (caught: unknown) {
      if (typeof caught === 'object' && caught !== null && 'status' in caught && (caught as { status: number }).status === 409) {
        setConflictOpen(true)
        setError('Conflicto de versiones: la configuración en el servidor cambió.')
      } else {
        setError(caught instanceof Error ? caught.message : 'Error al guardar configuración')
      }
      return null
    } finally {
      setSaving(false)
    }
  }

  // Hot-apply configuration
  const handleApply = async () => {
    if (!credentials || !config) return
    setApplying(true)
    setError(null)
    setSuccessMessage(null)
    try {
      // If dirty, save first
      let currentCfg = config
      if (isDraftDirty) {
        const saved = await handleSave()
        if (!saved) return
        currentCfg = saved
      }
      const applied = await applyConfig(apiUrl, credentials)
      onConfigUpdated(applied)
      setSuccessMessage(`Configuración aplicada en caliente con éxito (v${applied.active_version} activa).`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al aplicar configuración')
    } finally {
      setApplying(false)
    }
  }

  // Reconcile conflict by reloading from server
  const handleReloadServer = async () => {
    if (!credentials) return
    try {
      const latest = await getConfig(apiUrl, credentials)
      onConfigUpdated(latest)
      setCategories(latest.inventory.categories)
      setInventoryExample(Boolean(latest.inventory.example))
      setInventoryConfirmed(Boolean(latest.settings.inventory_confirmed))
      setConflictOpen(false)
      setError(null)
      setSuccessMessage('Configuración recargada desde el servidor.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Error al recargar')
    }
  }

  // 3x3 Grid Map by LED index (0..8)
  const gridCells = useMemo(() => {
    return Array.from({ length: 9 }, (_, ledIdx) => {
      const tool = categories.find((c) => c.led_index === ledIdx)
      return {
        led_index: ledIdx,
        pin: ledIdx + 2,
        tool,
      }
    })
  }, [categories])

  return (
    <section className="tool-manager-view" aria-label="Administración de Herramientas">
      <header className="section-header">
        <div>
          <p className="eyebrow">Configuración</p>
          <h2>Herramientas e Inventario Físico</h2>
        </div>
        <div className="version-status-pills">
          <span className={`badge ${isDraftDirty ? 'badge-warning' : 'badge-ok'}`}>
            {isDraftDirty ? 'Borrador sin guardar' : 'Sincronizado'}
          </span>
          <span className="badge badge-info">
            Guardado: v{config?.saved_version ?? 1} · Activo: v{config?.active_version ?? 1}
          </span>
        </div>
      </header>

      {/* Notifications */}
      {error && (
        <div className="alert alert-error" role="alert">
          <strong>Atención:</strong> {error}
          {conflictOpen && (
            <div className="conflict-actions">
              <button className="button button-sm button-secondary" onClick={() => void handleReloadServer()}>
                Recargar desde el servidor (descartar borrador)
              </button>
            </div>
          )}
        </div>
      )}
      {successMessage && (
        <div className="alert alert-info" role="status">
          {successMessage}
        </div>
      )}
      {!isAdmin && (
        <div className="alert alert-warning" role="status">
          <strong>Modo de solo lectura:</strong> Tu sesión actual no tiene rol de Administrador. Las modificaciones de herramientas están deshabilitadas.
        </div>
      )}

      {/* Confirmation & Global Actions */}
      <div className="inventory-actions-bar">
        <div className="confirmation-status">
          <span className={`status-icon ${inventoryConfirmed ? 'ok' : 'pending'}`} aria-hidden="true">
            {inventoryConfirmed ? '✓' : '⚠'}
          </span>
          <div>
            <strong>{inventoryConfirmed ? 'Inventario real confirmado' : 'Inventario de ejemplo / pendiente'}</strong>
            <p>
              {inventoryConfirmed
                ? 'Las 9 herramientas corresponden al organizador real.'
                : 'Configura las nueve herramientas y presiona "Confirmar inventario real" para habilitar el modo real.'}
            </p>
          </div>
        </div>

        <div className="action-buttons">
          {!inventoryConfirmed && (
            <button
              className="button button-primary"
              onClick={handleConfirmInventory}
              disabled={!isAdmin || saving || applying}
            >
              Confirmar inventario real
            </button>
          )}
          <button
            className="button button-secondary"
            onClick={() => void handleSave()}
            disabled={!isAdmin || !isDraftDirty || saving || applying}
          >
            {saving ? 'Guardando…' : 'Guardar borrador'}
          </button>
          <button
            className="button button-accent"
            onClick={() => void handleApply()}
            disabled={!isAdmin || saving || applying}
          >
            {applying ? 'Aplicando…' : 'Guardar y aplicar'}
          </button>
        </div>
      </div>

      {/* Interactive Physical Grid (3x3 Map) */}
      <div className="physical-organizer-section">
        <div className="section-subheading">
          <h3>Mapa Físico del Organizador (LEDs 0–8 / D2–D10)</h3>
          <p>Toca una celda para abrir y editar la herramienta vinculada.</p>
        </div>
        <div className="organizer-grid" role="grid" aria-label="Cuadrícula del organizador">
          {gridCells.map((cell) => {
            const isSelected = cell.tool?.class_id === focusedClassId
            const displayName = cell.tool?.display_name || cell.tool?.name || `LED ${cell.led_index}`
            return (
              <button
                key={cell.led_index}
                type="button"
                className={`grid-cell ${isSelected ? 'selected' : ''}`}
                onClick={() => cell.tool && scrollToCard(cell.tool.class_id)}
                aria-label={`LED ${cell.led_index}, Pin D${cell.pin}: ${displayName}`}
              >
                <div className="grid-cell-header">
                  <span className="led-badge">LED {cell.led_index} · D{cell.pin}</span>
                </div>
                <strong className="grid-tool-name">{displayName}</strong>
                <span className="grid-tool-loc">{cell.tool?.location || 'Sin ubicación'}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Guide for Visual Descriptions */}
      <aside className="description-guide-card">
        <h4>Guía para describir rasgos visibles (Zero-Shot)</h4>
        <p>
          EmbeddingGemma compara las fotografías con estas descripciones. Para máxima precisión zero-shot, detalla:
        </p>
        <ul>
          <li><strong>Forma:</strong> Silueta general (alargada, en T, semicircular, cabeza cuadrada).</li>
          <li><strong>Materiales y textura:</strong> Acero cromado, goma estriada, plástico opaco.</li>
          <li><strong>Color:</strong> Colores contrastantes del mango, punta y cuerpo.</li>
          <li><strong>Partes distintivas:</strong> Mordazas dentadas, muesca sacaclavos, doble punta.</li>
          <li><strong>Diferencias:</strong> Rasgos que la distinguen de las otras 8 herramientas de la bandeja.</li>
        </ul>
      </aside>

      {/* Nine Editable Tool Cards */}
      <div className="tool-cards-list">
        {categories.map((tool) => {
          const isFocused = tool.class_id === focusedClassId
          const isComplete = tool.name.trim() !== '' && tool.descriptions.length > 0
          const toolRefs = references[tool.class_id] || []
          const isPhotosOpen = photoOpenClassId === tool.class_id

          return (
            <article
              key={tool.class_id}
              ref={(el) => { cardRefs.current[tool.class_id] = el }}
              className={`tool-card ${isFocused ? 'card-focused' : ''}`}
              aria-label={`Herramienta ${tool.display_name || tool.name}`}
            >
              <div className="tool-card-header">
                <div className="tool-title-group">
                  <span className="class-id-badge" title="Identificador interno inmutable">{tool.class_id}</span>
                  <h4>{tool.display_name || tool.name}</h4>
                </div>
                <div className="tool-badges">
                  <span className={`badge ${isComplete ? 'badge-ok' : 'badge-warning'}`}>
                    {isComplete ? 'Preparada' : 'Incompleta'}
                  </span>
                  <span className="badge badge-info">
                    LED {tool.led_index} · D{tool.pin}
                  </span>
                  {tool.reference_count ? (
                    <span className="badge badge-accent">{tool.reference_count} fotos</span>
                  ) : null}
                </div>
              </div>

              <div className="tool-card-body">
                {/* Form fields */}
                <div className="form-row">
                  <label>
                    Nombre visible (UI)
                    <input
                      type="text"
                      value={tool.display_name ?? ''}
                      onChange={(e) => handleDisplayNameChange(tool.class_id, e.target.value)}
                      placeholder="Ej. Martillo de uña"
                      disabled={!isAdmin}
                    />
                  </label>
                  <label>
                    Ubicación física
                    <input
                      type="text"
                      value={tool.location}
                      onChange={(e) => handleLocationChange(tool.class_id, e.target.value)}
                      placeholder="Ej. Casillero A1"
                      disabled={!isAdmin}
                    />
                  </label>
                </div>

                <div className="form-row">
                  <label>
                    Nombre para IA (Clasificación)
                    <input
                      type="text"
                      value={tool.name}
                      onChange={(e) => handleSemanticNameChange(tool.class_id, e.target.value)}
                      placeholder="Ej. martillo_de_una"
                      disabled={!isAdmin}
                    />
                    <small className="field-hint">Nombre canónico usado para embeddings de texto.</small>
                  </label>
                  <label>
                    LED asignado (Intercambio automático)
                    <select
                      value={tool.led_index}
                      onChange={(e) => handleLedChange(tool.class_id, parseInt(e.target.value, 10))}
                      disabled={!isAdmin}
                      aria-label="Selector de LED"
                    >
                      {Array.from({ length: 9 }, (_, idx) => (
                        <option key={idx} value={idx}>
                          LED {idx} · Pin D{idx + 2}
                        </option>
                      ))}
                    </select>
                    <small className="field-hint">Al cambiar el LED, se intercambia automáticamente con la otra herramienta.</small>
                  </label>
                </div>

                {/* Descriptions List */}
                <div className="descriptions-group">
                  <label>Descripciones visuales ({tool.descriptions.length})</label>
                  {tool.descriptions.length === 0 ? (
                    <p className="empty-descs">Sin descripciones visuales. Añade al menos una para zero-shot.</p>
                  ) : (
                    <ul className="desc-list">
                      {tool.descriptions.map((desc, dIdx) => (
                        <li key={dIdx} className="desc-item">
                          <span>{desc}</span>
                          {isAdmin && (
                            <button
                              type="button"
                              className="button-icon-danger"
                              onClick={() => handleRemoveDescription(tool.class_id, dIdx)}
                              aria-label={`Eliminar rasgo: ${desc}`}
                            >
                              ✕
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}

                  {isAdmin && (
                    <div className="add-desc-row">
                      <input
                        type="text"
                        value={newDescInput[tool.class_id] || ''}
                        onChange={(e) => setNewDescInput({ ...newDescInput, [tool.class_id]: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            handleAddDescription(tool.class_id)
                          }
                        }}
                        placeholder="Nuevo rasgo visible (presiona Enter o Añadir)"
                      />
                      <button
                        type="button"
                        className="button button-sm button-secondary"
                        onClick={() => handleAddDescription(tool.class_id)}
                      >
                        Añadir
                      </button>
                    </div>
                  )}
                </div>

                {/* Reference Photos Collapsible */}
                <div className="photos-collapsible">
                  <button
                    type="button"
                    className="button button-sm button-secondary photos-toggle-btn"
                    onClick={() => void togglePhotos(tool.class_id)}
                    aria-expanded={isPhotosOpen}
                  >
                    <span>📷 {isPhotosOpen ? 'Ocultar fotografías' : 'Ver / Subir fotografías'}</span>
                    <span className="badge badge-sm">{tool.reference_count || 0}</span>
                  </button>

                  {isPhotosOpen && (
                    <div className="photos-panel">
                      {loadingRefs === tool.class_id && <p className="loading-copy">Cargando fotos…</p>}
                      {uploadProgress && <div className="upload-progress-copy">{uploadProgress}</div>}

                      {toolRefs.length > 0 ? (
                        <div className="photo-thumbnails-grid">
                          {toolRefs.map((photo) => (
                            <div key={photo.image_id} className="photo-thumb-card">
                              <img src={photo.thumbnail_data_url} alt="Referencia visual" className="photo-thumb-img" />
                              {isAdmin && (
                                <button
                                  type="button"
                                  className="thumb-delete-btn"
                                  onClick={() => void handleDeletePhoto(tool.class_id, photo.image_id)}
                                  aria-label="Eliminar foto"
                                >
                                  ✕
                                </button>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="empty-copy">No hay fotografías de referencia subidas para esta herramienta.</p>
                      )}

                      {isAdmin && (
                        <div className="photo-upload-actions">
                          <label className="button button-sm button-primary upload-label">
                            <span>📷 Tomar foto</span>
                            <input
                              type="file"
                              accept="image/jpeg,image/png,image/webp"
                              capture="environment"
                              onChange={(e) => {
                                const files = Array.from(e.target.files || [])
                                e.target.value = ''
                                void handleUploadPhotos(tool.class_id, files)
                              }}
                            />
                          </label>
                          <label className="button button-sm button-secondary upload-label">
                            <span>📁 Subir archivos</span>
                            <input
                              type="file"
                              accept="image/jpeg,image/png,image/webp"
                              multiple
                              onChange={(e) => {
                                const files = Array.from(e.target.files || [])
                                e.target.value = ''
                                void handleUploadPhotos(tool.class_id, files)
                              }}
                            />
                          </label>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
