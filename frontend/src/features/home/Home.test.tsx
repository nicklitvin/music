import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { ACCESS_TOKEN, signIn, signOut } from '../../lib/auth'
import { Home } from './Home'

function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/scores" element={<p>Your library</p>} />
        <Route path="/signin" element={<p>token please</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Home', () => {
  beforeEach(() => signOut())

  it('sends a new visitor to the token gate when Start is pressed', async () => {
    const user = userEvent.setup()
    renderHome()

    await user.click(screen.getByRole('button', { name: /start/i }))

    expect(await screen.findByText('token please')).toBeInTheDocument()
  })

  it('goes straight to the library once the token has been given', async () => {
    signIn(ACCESS_TOKEN)
    const user = userEvent.setup()
    renderHome()

    await user.click(screen.getByRole('button', { name: /start/i }))

    expect(await screen.findByText('Your library')).toBeInTheDocument()
  })
})
