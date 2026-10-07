import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { PATCH_NOTES } from '../../lib/patchNotes'
import { BUILD_DATE } from '../../lib/version'
import { VersionChip } from '../../components/VersionChip'
import { PatchNotes } from './PatchNotes'

describe('patch notes', () => {
  it('lists every release, newest first', () => {
    render(
      <MemoryRouter>
        <PatchNotes />
      </MemoryRouter>,
    )

    const dates = PATCH_NOTES.map((note) => note.date)
    expect([...dates].sort().reverse()).toEqual(dates)

    for (const note of PATCH_NOTES) {
      expect(screen.getByText(note.title)).toBeInTheDocument()
    }
  })

  it('shows the build it is running', () => {
    render(
      <MemoryRouter>
        <PatchNotes />
      </MemoryRouter>,
    )
    expect(screen.getByText(`Running v${BUILD_DATE}`)).toBeInTheDocument()
  })

  it('renders each change as its own point', () => {
    render(
      <MemoryRouter>
        <PatchNotes />
      </MemoryRouter>,
    )
    for (const change of PATCH_NOTES[0].changes) {
      expect(screen.getByText(change)).toBeInTheDocument()
    }
  })
})

describe('VersionChip', () => {
  it('shows the build date and opens the notes', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<VersionChip />} />
          <Route path="/whats-new" element={<p>notes page</p>} />
        </Routes>
      </MemoryRouter>,
    )

    const chip = screen.getByRole('button', { name: `v${BUILD_DATE}` })
    expect(chip).toBeInTheDocument()

    await user.click(chip)
    expect(await screen.findByText('notes page')).toBeInTheDocument()
  })

  it('reports a date, in ISO form', () => {
    expect(BUILD_DATE).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
