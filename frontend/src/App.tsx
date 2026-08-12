import { Route, Routes } from 'react-router-dom'
import { Dashboard } from './features/dashboard/Dashboard'
import { UploadScore } from './features/upload/UploadScore'
import { ScoreViewer } from './features/viewer/ScoreViewer'
import './App.css'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/upload" element={<UploadScore />} />
      <Route path="/scores/:scoreId" element={<ScoreViewer />} />
    </Routes>
  )
}

export default App
