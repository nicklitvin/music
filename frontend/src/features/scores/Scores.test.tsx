import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { db, saveScore } from '../../lib/db'
import { UploadQueueProvider } from '../../lib/uploadQueue'
import { Scores } from './Scores'
import type { ScoreRecord } from '../../lib/types'

function renderScores() {
  return render(
    <BrowserRouter>
      <UploadQueueProvider>
        <Scores />
      </UploadQueueProvider>
    </BrowserRouter>,
  )
}

function makeScore(overrides: Partial<ScoreRecord> = {}): ScoreRecord {
  return {
    id: crypto.randomUUID(),
    title: 'Test Score',
    uploadDate: new Date().toISOString(),
    musicXml: '<score-partwise />',
    boundingBoxes: [],
    pages: [{ pageIndex: 0, image: new Blob(['x']), width: 100, height: 140 }],
    sortOrder: 0,
    ...overrides,
  }
}

describe('Scores', () => {
  beforeEach(async () => {
    await db.scores.clear()
  })

  it('shows an empty state when there are no scores', async () => {
    renderScores()

    expect(await screen.findByText(/no scores yet/i)).toBeInTheDocument()
  })

  it('lists saved scores with their page count', async () => {
    await saveScore(makeScore({ title: 'Moonlight Sonata' }))

    renderScores()

    expect(await screen.findByText('Moonlight Sonata')).toBeInTheDocument()
    expect(screen.getByText('1 page')).toBeInTheDocument()
  })

  it('asks for confirmation before deleting, and only deletes on confirm', async () => {
    const user = userEvent.setup()
    await saveScore(makeScore({ title: 'Moonlight Sonata' }))
    renderScores()
    await screen.findByText('Moonlight Sonata')

    await user.click(screen.getByRole('button', { name: /delete moonlight sonata/i }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.getByText('Moonlight Sonata')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /delete moonlight sonata/i }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))

    expect(screen.queryByText('Moonlight Sonata')).not.toBeInTheDocument()
  })
})
