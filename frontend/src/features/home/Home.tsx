import { useNavigate } from 'react-router-dom'

export function Home() {
  const navigate = useNavigate()

  return (
    <div className="home">
      <div className="home-content">
        <div className="home-icon" aria-hidden="true">
          <svg viewBox="0 0 96 96" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="14" y="10" width="52" height="68" rx="6" className="home-icon-page" />
            <path d="M22 24h36M22 34h36M22 44h24" className="home-icon-line" strokeWidth="3" strokeLinecap="round" />
            <circle cx="70" cy="62" r="16" className="home-icon-note-head" />
            <path d="M84 62V26l10-4v34" className="home-icon-note-stem" strokeWidth="4" strokeLinecap="round" />
          </svg>
        </div>
        <h1>Sheet Music Tracker</h1>
        <p>
          Upload your sheet music, play along on piano, and watch the page follow your playing automatically --
          no manual scrolling, no lost place.
        </p>
      </div>

      <div className="home-start-row">
        <button className="btn btn-primary home-start" onClick={() => navigate('/scores')}>
          Start
        </button>
      </div>
    </div>
  )
}
