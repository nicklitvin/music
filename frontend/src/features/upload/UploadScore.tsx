import { useNavigate } from 'react-router-dom'
import { BottomNav } from '../../components/BottomNav'
import { useUploadQueue } from '../../lib/uploadQueue'

const INFO_CONTENT = (
  <p>
    Choose a PDF of piano sheet music. It's parsed into individual notes and page images, stored only on this
    device, then you're taken back to your library where you can watch it process.
  </p>
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
        Note recognition runs on our server and can take a few minutes per page -- you'll see it processing in
        your scores list.
      </p>
      <BottomNav infoTitle="Upload Sheet Music" infoContent={INFO_CONTENT} />
    </div>
  )
}
