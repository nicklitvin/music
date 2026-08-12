import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { db } from '../../lib/db'
import { Dashboard } from './Dashboard'

describe('Dashboard', () => {
  beforeEach(async () => {
    await db.scores.clear()
  })

  it('shows an empty state when there are no scores', async () => {
    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>,
    )

    expect(await screen.findByText(/no scores yet/i)).toBeInTheDocument()
  })
})
