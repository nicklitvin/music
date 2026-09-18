import { beforeEach, describe, expect, it } from 'vitest'
import { db, deleteScore, listScores, nextSortOrderForNewScore, reorderScores, saveScore } from './db'
import type { ScoreRecord } from './types'

function makeScore(overrides: Partial<ScoreRecord> = {}): ScoreRecord {
  return {
    id: crypto.randomUUID(),
    title: 'Test Score',
    uploadDate: new Date().toISOString(),
    musicXml: '<score-partwise />',
    boundingBoxes: [],
    pages: [],
    ...overrides,
  }
}

describe('scores table', () => {
  beforeEach(async () => {
    await db.scores.clear()
  })

  it('saves and lists scores', async () => {
    const score = makeScore({ title: 'Moonlight Sonata' })
    await saveScore(score)

    const scores = await listScores()
    expect(scores).toHaveLength(1)
    expect(scores[0].title).toBe('Moonlight Sonata')
  })

  it('deletes a score', async () => {
    const score = makeScore()
    await saveScore(score)
    await deleteScore(score.id)

    const scores = await listScores()
    expect(scores).toHaveLength(0)
  })

  it('lists scores ordered by sortOrder ascending', async () => {
    await saveScore(makeScore({ title: 'Second', sortOrder: 1 }))
    await saveScore(makeScore({ title: 'First', sortOrder: 0 }))
    await saveScore(makeScore({ title: 'Third', sortOrder: 2 }))

    const scores = await listScores()
    expect(scores.map((s) => s.title)).toEqual(['First', 'Second', 'Third'])
  })

  it('gives a new score a sortOrder before every existing one, so it lands on top', async () => {
    await saveScore(makeScore({ title: 'Existing', sortOrder: 5 }))

    const next = await nextSortOrderForNewScore()
    await saveScore(makeScore({ title: 'New', sortOrder: next }))

    const scores = await listScores()
    expect(scores[0].title).toBe('New')
  })

  it('persists a drag-reorder', async () => {
    const a = makeScore({ title: 'A', sortOrder: 0 })
    const b = makeScore({ title: 'B', sortOrder: 1 })
    const c = makeScore({ title: 'C', sortOrder: 2 })
    await saveScore(a)
    await saveScore(b)
    await saveScore(c)

    await reorderScores([c.id, a.id, b.id])

    const scores = await listScores()
    expect(scores.map((s) => s.title)).toEqual(['C', 'A', 'B'])
  })
})
