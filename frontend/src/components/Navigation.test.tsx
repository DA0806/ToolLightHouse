import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Navigation } from './Navigation'

describe('Navigation component', () => {
  it('renders all five sections on desktop and mobile', () => {
    const onChangeTab = vi.fn()
    render(<Navigation activeTab="inspection" onChangeTab={onChangeTab} />)

    expect(screen.getAllByText('Inspección').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Herramientas').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Calibración').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Arduino').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Sistema').length).toBeGreaterThanOrEqual(1)
  })

  it('calls onChangeTab when a tab is clicked', () => {
    const onChangeTab = vi.fn()
    render(<Navigation activeTab="inspection" onChangeTab={onChangeTab} />)

    const toolsTabs = screen.getAllByRole('button', { name: /Herramientas/i })
    fireEvent.click(toolsTabs[0])
    expect(onChangeTab).toHaveBeenCalledWith('tools')
  })

  it('renders persistent "Volver a Inspección" button only on non-inspection tabs', () => {
    const onChangeTab = vi.fn()
    const { rerender } = render(<Navigation activeTab="inspection" onChangeTab={onChangeTab} />)
    expect(screen.queryByRole('button', { name: /Volver a Inspección/i })).not.toBeInTheDocument()

    rerender(<Navigation activeTab="tools" onChangeTab={onChangeTab} />)
    const backBtn = screen.getByRole('button', { name: /Volver a Inspección/i })
    expect(backBtn).toBeInTheDocument()

    fireEvent.click(backBtn)
    expect(onChangeTab).toHaveBeenCalledWith('inspection')
  })

  it('renders pending changes banner when config is dirty or applying', () => {
    const onApply = vi.fn()
    const { rerender } = render(
      <Navigation
        activeTab="inspection"
        onChangeTab={vi.fn()}
        isDirty={true}
        savedVersion={3}
        activeVersion={2}
        onApplyConfig={onApply}
      />
    )

    expect(screen.getByRole('status', { name: 'Estado de aplicación' })).toBeInTheDocument()
    expect(screen.getByText(/Versión guardada v3 · Activa v2/i)).toBeInTheDocument()
    const applyBtn = screen.getByRole('button', { name: 'Guardar y aplicar' })
    fireEvent.click(applyBtn)
    expect(onApply).toHaveBeenCalledTimes(1)

    // When task is running
    rerender(
      <Navigation
        activeTab="inspection"
        onChangeTab={vi.fn()}
        isDirty={false}
        savedVersion={3}
        activeVersion={2}
        task={{ state: 'running', stage: 'encoding_embeddings', progress: 0.45, error: null }}
        onApplyConfig={onApply}
      />
    )
    expect(screen.getByText(/Aplicando configuración en caliente…/i)).toBeInTheDocument()
    expect(screen.getByText(/Etapa: encoding_embeddings \(45%\)/i)).toBeInTheDocument()
  })
})
