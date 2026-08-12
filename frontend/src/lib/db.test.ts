import { beforeEach, describe, expect, it } from 'vitest'
import { db, deleteScore, listScores, saveScore } from './db'
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
})
