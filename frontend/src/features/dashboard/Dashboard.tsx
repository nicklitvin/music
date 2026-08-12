import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { deleteScore, listScores } from '../../lib/db'
import type { ScoreRecord } from '../../lib/types'

export function Dashboard() {
  const [scores, setScores] = useState<ScoreRecord[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listScores()
      .then(setScores)
      .finally(() => setLoading(false))
  }, [])

  async function handleDelete(id: string) {
    await deleteScore(id)
    setScores((prev) => prev.filter((score) => score.id !== id))
  }

  if (loading) return <p>Loading your library…</p>

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Your Scores</h1>
        <Link className="button" to="/upload">
          Upload Sheet Music
        </Link>
      </header>

      {scores.length === 0 ? (
        <p>No scores yet. Upload a PDF to get started.</p>
      ) : (
        <ul className="score-grid">
          {scores.map((score) => (
            <li key={score.id} className="score-card">
              <Link to={`/scores/${score.id}`}>
                <strong>{score.title}</strong>
                <span>{new Date(score.uploadDate).toLocaleDateString()}</span>
              </Link>
              <button onClick={() => handleDelete(score.id)}>Delete</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
