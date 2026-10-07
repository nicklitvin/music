import { useNavigate } from 'react-router-dom'
import { BUILD_DATE } from '../lib/version'

/** The build currently being served, bottom-right, linking to what changed. */
export function VersionChip() {
  const navigate = useNavigate()

  return (
    <button className="version-chip" onClick={() => navigate('/whats-new')} title="What's new">
      v{BUILD_DATE}
    </button>
  )
}
