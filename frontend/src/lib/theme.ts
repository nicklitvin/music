// Explicit light/dark override on top of the OS preference (see
// index.css's `prefers-color-scheme` block). 'system' means "no override,
// follow the OS" -- the common and default case.
export type ThemePreference = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'theme-preference'

export function getThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // Private browsing / storage disabled -- fall through to the default.
  }
  return 'system'
}

export function applyThemePreference(preference: ThemePreference): void {
  const root = document.documentElement
  if (preference === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', preference)
}

export function setThemePreference(preference: ThemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference)
  } catch {
    // Ignore -- the preference just won't survive a reload this session.
  }
  applyThemePreference(preference)
}
