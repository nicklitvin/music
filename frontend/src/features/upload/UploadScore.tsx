import { useNavigate } from 'react-router-dom'
import { BottomNav } from '../../components/BottomNav'
import { useUploadQueue } from '../../lib/uploadQueue'

const INFO_CONTENT = (
  <>
    <p>
      Choose a PDF of piano sheet music. It's parsed into individual notes and page images, stored only on this
      device, then you're taken back to your library where you can watch it process.
    </p>
    <p>
      Note recognition runs a model over every page and takes <strong>about 5 minutes per page</strong> — a
      10-page sheet is roughly an hour. Your library shows an estimated time remaining while it works, and you
      can leave the page and come back.
    </p>
  </>
)

export function UploadScore() {
  const navigate = useNavigate()
  const { startUpload } = useUploadQueue()

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    // Processing (OMR can take minutes) happens in the background via the
    // upload queue, so we can leave this page immediately -- the new item
    // shows up at the top of the scores list with a spinner until it's done.
    startUpload(file)
    navigate('/scores')
  }

  return (
    <div className="upload">
      <header className="viewer-header">
        <h1>Upload Sheet Music</h1>
      </header>
      <label className="upload-picker">
        <input type="file" accept="application/pdf" onChange={handleFileChange} />
        <span className="btn btn-primary">Choose a PDF</span>
      </label>
      <p className="subtle-text">
        Note recognition takes about 5 minutes per page, so a 10-page sheet is roughly an hour. You'll see an
        estimated time remaining in your scores list, and you can leave this page while it works.
      </p>
      <BottomNav infoTitle="Upload Sheet Music" infoContent={INFO_CONTENT} />
    </div>
  )
}
