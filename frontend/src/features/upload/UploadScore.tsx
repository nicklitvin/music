import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { saveScore } from '../../lib/db'
import { processScore } from '../../lib/api'

export function UploadScore() {
  const navigate = useNavigate()
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return

    setIsProcessing(true)
    setError(null)

    try {
      const scoreId = crypto.randomUUID()
      const score = await processScore(file, scoreId)
      await saveScore(score)
      navigate(`/scores/${score.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setIsProcessing(false)
    }
  }

  return (
    <div className="upload">
      <h1>Upload Sheet Music</h1>
      <input type="file" accept="application/pdf" onChange={handleFileChange} disabled={isProcessing} />
      {isProcessing && <p>Processing score… OMR runs for real per page, so this can take several minutes per page.</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
