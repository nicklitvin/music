import Dexie, { type EntityTable } from 'dexie'
import type { ScoreRecord } from './types'

class ScoreDatabase extends Dexie {
  scores!: EntityTable<ScoreRecord, 'id'>

  constructor() {
    super('sheet-music-tracker')
    this.version(1).stores({
      scores: 'id, title, uploadDate',
    })
    // v2 adds manual ordering (drag handles on the scores list). Existing
    // rows get a sortOrder that preserves their old newest-first order, so
    // upgrading doesn't reshuffle anyone's list.
    this.version(2)
      .stores({
        scores: 'id, title, uploadDate, sortOrder',
      })
      .upgrade(async (tx) => {
        const rows = await tx.table('scores').orderBy('uploadDate').reverse().toArray()
        await Promise.all(rows.map((row, index) => tx.table('scores').update(row.id, { sortOrder: index })))
      })
  }
}

export const db = new ScoreDatabase()

export async function listScores(): Promise<ScoreRecord[]> {
  const scores = await db.scores.toArray()
  return scores.sort((a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0))
}

export async function getScore(id: string): Promise<ScoreRecord | undefined> {
  return db.scores.get(id)
}

// New items belong at the top of the list -- one less than the current
// smallest sortOrder, so they sort first regardless of how many scores
// already exist.
export async function nextSortOrderForNewScore(): Promise<number> {
  const first = await db.scores.orderBy('sortOrder').first()
  return (first?.sortOrder ?? 0) - 1
}

export async function saveScore(score: ScoreRecord): Promise<void> {
  await db.scores.put(score)
}

// Persists a drag-reorder: `orderedIds` is the full list of score ids in
// their new top-to-bottom order.
export async function reorderScores(orderedIds: string[]): Promise<void> {
  await db.transaction('rw', db.scores, async () => {
    await Promise.all(orderedIds.map((id, index) => db.scores.update(id, { sortOrder: index })))
  })
}

export async function deleteScore(id: string): Promise<void> {
  await db.scores.delete(id)
}
