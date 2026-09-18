import { useState } from 'react'
import type { ReactNode } from 'react'
import { Modal } from './Modal'
import { SettingsModal } from './SettingsModal'

export interface PillAction {
  key: string
  label: string
  icon: string
  onClick: () => void
  disabled?: boolean
  emphasized?: boolean
}

interface BottomNavProps {
  infoTitle: string
  infoContent: ReactNode
  // Page-specific actions, rendered between the always-present Info and
  // Settings pills (e.g. Upload on the scores page, Previous/Next/Download
  // on the sheet page).
  actions?: PillAction[]
}

// The app's whole navigation model: a pill fixed to the bottom of the
// screen, present on every screen after the home page. Info and Settings
// are always there; each page adds its own actions in the middle.
export function BottomNav({ infoTitle, infoContent, actions = [] }: BottomNavProps) {
  const [openModal, setOpenModal] = useState<'info' | 'settings' | null>(null)

  return (
    <>
      <nav className="bottom-pill" aria-label="Main navigation">
        <button className="pill-btn" onClick={() => setOpenModal('info')}>
          <span aria-hidden>ℹ️</span>
          <span className="pill-label">Info</span>
        </button>
        {actions.map((action) => (
          <button
            key={action.key}
            className={`pill-btn${action.emphasized ? ' pill-btn-emphasized' : ''}`}
            onClick={action.onClick}
            disabled={action.disabled}
          >
            <span aria-hidden>{action.icon}</span>
            <span className="pill-label">{action.label}</span>
          </button>
        ))}
        <button className="pill-btn" onClick={() => setOpenModal('settings')}>
          <span aria-hidden>⚙️</span>
          <span className="pill-label">Settings</span>
        </button>
      </nav>

      {openModal === 'info' && (
        <Modal title={infoTitle} onClose={() => setOpenModal(null)}>
          {infoContent}
        </Modal>
      )}
      {openModal === 'settings' && <SettingsModal onClose={() => setOpenModal(null)} />}
    </>
  )
}
