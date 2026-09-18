import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { UploadQueueProvider } from './lib/uploadQueue.tsx'
import { applyThemePreference, getThemePreference } from './lib/theme.ts'

applyThemePreference(getThemePreference())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <UploadQueueProvider>
        <App />
      </UploadQueueProvider>
    </BrowserRouter>
  </StrictMode>,
)
