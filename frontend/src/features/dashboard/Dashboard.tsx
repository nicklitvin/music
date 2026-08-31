import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { deleteScore, listScores, saveScore } from '../../lib/db'
import { loadSampleScore } from '../../lib/api'
import type { ScoreRecord } from '../../lib/types'

export function Dashboard() {
  const navigate = useNavigate()
  const [scores, setScores] = useState<ScoreRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [isLoadingSample, setIsLoadingSample] = useState(false)
  const [sampleError, setSampleError] = useState<string | null>(null)

  useEffect(() => {
    listScores()
      .then(setScores)
      .finally(() => setLoading(false))
  }, [])

  async function handleDelete(id: string) {
    await deleteScore(id)
    setScores((prev) => prev.filter((score) => score.id !== id))
  }

  async function handleLoadSample() {
    setIsLoadingSample(true)
    setSampleError(null)
    try {
      const score = await loadSampleScore(0)
      await saveScore(score)
      navigate(`/scores/${score.id}`)
    } catch (err) {
      setSampleError(err instanceof Error ? err.message : 'Failed to load sample score')
    } finally {
      setIsLoadingSample(false)
    }
  }

  if (loading) return <p>Loading your library…</p>

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Your Scores</h1>
        <div className="viewer-header-actions">
          <button onClick={handleLoadSample} disabled={isLoadingSample} title="Loads pre-computed notes for aLIEz.pdf page 1 -- instant, no OMR re-run">
            {isLoadingSample ? 'Loading…' : 'Load Sample: aLIEz (page 1)'}
          </button>
          <Link className="button" to="/upload">
            Upload Sheet Music
          </Link>
        </div>
      </header>
      {sampleError && <p role="alert">{sampleError}</p>}

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
