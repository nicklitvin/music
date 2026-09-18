import { useEffect, useState } from 'react'
import { Modal } from './Modal'
import { getThemePreference, setThemePreference, type ThemePreference } from '../lib/theme'

const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 MB'
  const mb = bytes / (1024 * 1024)
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`
}

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [theme, setTheme] = useState<ThemePreference>(getThemePreference)
  const [usage, setUsage] = useState<{ used: number; quota: number } | null>(null)

  useEffect(() => {
    navigator.storage
      ?.estimate?.()
      .then((estimate) => setUsage({ used: estimate.usage ?? 0, quota: estimate.quota ?? 0 }))
      .catch(() => undefined)
  }, [])

  function chooseTheme(next: ThemePreference) {
    setTheme(next)
    setThemePreference(next)
  }

  return (
    <Modal title="Settings" onClose={onClose}>
      <section className="settings-section">
        <h3>Appearance</h3>
        <div className="segmented" role="radiogroup" aria-label="Theme">
          {THEME_OPTIONS.map((option) => (
            <button
              key={option.value}
              role="radio"
              aria-checked={theme === option.value}
              className={theme === option.value ? 'is-active' : ''}
              onClick={() => chooseTheme(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <h3>Storage</h3>
        <p>
          Everything you upload -- page images, sheet music, and detected notes -- is stored only in this
          browser's IndexedDB, on this device. Nothing is uploaded to a server for storage.
        </p>
        {usage && (
          <p className="subtle-text">
            Using about {formatBytes(usage.used)} of {formatBytes(usage.quota)} available on this device.
          </p>
        )}
      </section>

      <section className="settings-section">
        <h3>Account</h3>
        <p className="subtle-text">Sign-in isn't available yet. Coming soon.</p>
      </section>
    </Modal>
  )
}
