import { Navigate, Route, Routes } from 'react-router-dom'
import type { ReactNode } from 'react'
import { Home } from './features/home/Home'
import { SignIn } from './features/auth/SignIn'
import { Scores } from './features/scores/Scores'
import { ScoreViewer } from './features/viewer/ScoreViewer'
import { PatchNotes } from './features/patchNotes/PatchNotes'
import { isSignedIn } from './lib/auth'
import './App.css'

// Everything behind the token gate. Checked per render rather than once at
// startup, so signing out takes effect immediately.
function RequireToken({ children }: { children: ReactNode }) {
  return isSignedIn() ? <>{children}</> : <Navigate to="/signin" replace />
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/signin" element={<SignIn />} />
      {/* Outside the token gate: knowing what changed isn't private, and
          it's reachable from the version chip on the sign-in screen. */}
      <Route path="/whats-new" element={<PatchNotes />} />
      <Route
        path="/scores"
        element={
          <RequireToken>
            <Scores />
          </RequireToken>
        }
      />
      <Route
        path="/scores/:scoreId"
        element={
          <RequireToken>
            <ScoreViewer />
          </RequireToken>
        }
      />
    </Routes>
  )
}

export default App
