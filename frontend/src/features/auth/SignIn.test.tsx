import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { isSignedIn, signOut } from '../../lib/auth'
import { SignIn } from './SignIn'

function renderSignIn(initial = '/signin') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/signin" element={<SignIn />} />
        <Route path="/scores" element={<p>library</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SignIn', () => {
  beforeEach(() => signOut())

  it('rejects a wrong token and stays put', async () => {
    const user = userEvent.setup()
    renderSignIn()

    await user.type(screen.getByLabelText(/access token/i), 'letmein')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('library')).not.toBeInTheDocument()
    expect(isSignedIn()).toBe(false)
  })

  it('accepts the right token and goes to the library', async () => {
    const user = userEvent.setup()
    renderSignIn()

    await user.type(screen.getByLabelText(/access token/i), 'mu1sicscr0ll')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText('library')).toBeInTheDocument()
    expect(isSignedIn()).toBe(true)
  })

  it('ignores surrounding whitespace, which is easy to paste in', async () => {
    const user = userEvent.setup()
    renderSignIn()

    await user.type(screen.getByLabelText(/access token/i), '  mu1sicscr0ll  ')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText('library')).toBeInTheDocument()
  })

  it('clears the error as soon as the reader edits the token again', async () => {
    const user = userEvent.setup()
    renderSignIn()
    const input = screen.getByLabelText(/access token/i)

    await user.type(input, 'nope')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    await user.type(input, 'x')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('cannot be submitted empty', async () => {
    renderSignIn()
    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })

  it('signing out revokes access again', async () => {
    const user = userEvent.setup()
    renderSignIn()
    await user.type(screen.getByLabelText(/access token/i), 'mu1sicscr0ll')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    expect(isSignedIn()).toBe(true)

    signOut()
    expect(isSignedIn()).toBe(false)
  })
})
