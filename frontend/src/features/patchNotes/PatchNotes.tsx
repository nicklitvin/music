import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { BUILD_DATE } from '../../lib/version'
import { PATCH_NOTES } from '../../lib/patchNotes'

function formatDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
}

export function PatchNotes() {
  const navigate = useNavigate()

  return (
    <div className="patch-notes">
      <header className="patch-notes-header">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={16} /> Back
        </button>
        <h1>What&rsquo;s new</h1>
        <p className="subtle-text">Running v{BUILD_DATE}</p>
      </header>

      <ol className="patch-notes-list">
        {PATCH_NOTES.map((note) => (
          <li key={note.date} className="patch-note">
            <time className="patch-note-date" dateTime={note.date}>
              {formatDate(note.date)}
            </time>
            <h2>{note.title}</h2>
            <ul>
              {note.changes.map((change) => (
                <li key={change}>{change}</li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  )
}
