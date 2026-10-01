import { Route, Routes } from 'react-router-dom'
import { Home } from './features/home/Home'
import { Scores } from './features/scores/Scores'
import { ScoreViewer } from './features/viewer/ScoreViewer'
import './App.css'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/scores" element={<Scores />} />
      <Route path="/scores/:scoreId" element={<ScoreViewer />} />
    </Routes>
  )
}

export default App
