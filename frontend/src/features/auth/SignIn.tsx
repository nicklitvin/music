import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { KeyRound } from 'lucide-react'
import { signIn } from '../../lib/auth'

export function SignIn() {
  const navigate = useNavigate()
  const [token, setToken] = useState('')
  const [failed, setFailed] = useState(false)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (signIn(token)) {
      navigate('/scores', { replace: true })
    } else {
      setFailed(true)
    }
  }

  return (
    <div className="signin">
      <form className="signin-card" onSubmit={handleSubmit}>
        <div className="signin-icon" aria-hidden="true">
          <KeyRound size={32} />
        </div>
        <h1>Enter your access token</h1>
        <p className="subtle-text">Your sheet music library is behind this.</p>

        <input
          className="signin-input"
          type="password"
          value={token}
          autoFocus
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          aria-label="Access token"
          aria-invalid={failed}
          placeholder="Access token"
          onChange={(event) => {
            setToken(event.target.value)
            setFailed(false)
          }}
        />
        {failed && (
          <p className="signin-error" role="alert">
            That token isn't right.
          </p>
        )}

        <button className="btn btn-primary signin-submit" type="submit" disabled={!token.trim()}>
          Continue
        </button>
      </form>
    </div>
  )
}
