import type { ActiveTab, ConfigTask } from '../types'

interface NavigationProps {
  activeTab: ActiveTab
  onChangeTab: (tab: ActiveTab) => void
  isDirty?: boolean
  hasLocalChanges?: boolean
  savedVersion?: number
  activeVersion?: number
  task?: ConfigTask | null
  onApplyConfig?: () => void
  applying?: boolean
}

export function Navigation({
  activeTab,
  onChangeTab,
  isDirty = false,
  hasLocalChanges = false,
  savedVersion = 1,
  activeVersion = 1,
  task = null,
  onApplyConfig,
  applying = false,
}: NavigationProps) {
  const tabs: Array<{ id: ActiveTab; label: string; icon: string }> = [
    { id: 'inspection', label: 'Inspección', icon: '⌖' },
    { id: 'tools', label: 'Herramientas', icon: '⚒' },
    { id: 'calibration', label: 'Calibración', icon: '⚙' },
    { id: 'arduino', label: 'Arduino', icon: '⚡' },
    { id: 'system', label: 'Sistema', icon: '🖥' },
  ]

  const showDirtyBanner = isDirty || hasLocalChanges || savedVersion !== activeVersion || (task && task.state === 'running')
  const isTaskRunning = task?.state === 'running' || applying

  return (
    <>
      {showDirtyBanner && (
        <aside className="pending-banner" role="status" aria-label="Estado de aplicación">
          <div className="pending-banner-content">
            <span className="pending-indicator" aria-hidden="true">●</span>
            <div>
              <strong>
                {isTaskRunning
                  ? 'Aplicando configuración en caliente…'
                  : hasLocalChanges
                  ? 'Cambios locales sin guardar'
                  : 'Configuración pendiente de aplicar'}
              </strong>
              <span>
                {isTaskRunning
                  ? `Etapa: ${task?.stage || 'procesando'} (${Math.round((task?.progress || 0) * 100)}%)`
                  : hasLocalChanges
                  ? `Borrador local modificado · Guardado v${savedVersion} · Activo v${activeVersion}`
                  : `Versión guardada v${savedVersion} · Activa v${activeVersion}`}
              </span>
            </div>
          </div>
          {onApplyConfig && !isTaskRunning && (
            <button
              className="button button-accent button-sm"
              onClick={onApplyConfig}
              disabled={isTaskRunning}
            >
              Guardar y aplicar
            </button>
          )}
        </aside>
      )}

      {activeTab !== 'inspection' && (
        <div className="back-inspection-container">
          <button
            className="button button-primary back-inspection-btn"
            onClick={() => onChangeTab('inspection')}
            aria-label="Volver a Inspección"
          >
            <span aria-hidden="true">←</span> Volver a Inspección
          </button>
        </div>
      )}

      {/* Desktop Navigation */}
      <nav className="desktop-nav" aria-label="Navegación de secciones (escritorio)">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => onChangeTab(tab.id)}
            aria-current={activeTab === tab.id ? 'page' : undefined}
          >
            <span className="tab-icon" aria-hidden="true">{tab.icon}</span>
            <span className="tab-label">{tab.label}</span>
          </button>
        ))}
      </nav>

      {/* Mobile Bottom Navigation */}
      <nav className="bottom-nav" aria-label="Navegación principal (móvil)">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`nav-item ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => onChangeTab(tab.id)}
            aria-current={activeTab === tab.id ? 'page' : undefined}
          >
            <span className="nav-icon" aria-hidden="true">{tab.icon}</span>
            <span className="nav-label">{tab.label}</span>
          </button>
        ))}
      </nav>
    </>
  )
}
