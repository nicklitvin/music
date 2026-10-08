import Dexie, { type EntityTable } from 'dexie'
import type { PendingUploadRecord, ScoreRecord } from './types'

class ScoreDatabase extends Dexie {
  scores!: EntityTable<ScoreRecord, 'id'>
  uploads!: EntityTable<PendingUploadRecord, 'id'>

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
    // v3 keeps uploads that are still being processed, PDF and all, so a
    // refresh (or closing the tab) mid-upload resumes it rather than
    // silently losing it.
    this.version(3).stores({
      scores: 'id, title, uploadDate, sortOrder',
      uploads: 'id, startedAt',
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

export async function listPendingUploads(): Promise<PendingUploadRecord[]> {
  return db.uploads.orderBy('startedAt').reverse().toArray()
}

export async function savePendingUpload(upload: PendingUploadRecord): Promise<void> {
  await db.uploads.put(upload)
}

export async function updatePendingUpload(id: string, changes: Partial<PendingUploadRecord>): Promise<void> {
  await db.uploads.update(id, changes)
}

export async function deletePendingUpload(id: string): Promise<void> {
  await db.uploads.delete(id)
}

// Saving the finished score and dropping its pending upload in one
// transaction, so a crash between the two can't leave both (a duplicate) or
// neither (a lost sheet). Does nothing if the upload was cancelled in the
// meantime, or another tab got there first.
export async function completePendingUpload(score: ScoreRecord): Promise<void> {
  await db.transaction('rw', db.scores, db.uploads, async () => {
    if (!(await db.uploads.get(score.id))) return
    const first = await db.scores.orderBy('sortOrder').first()
    await db.scores.put({ ...score, sortOrder: (first?.sortOrder ?? 0) - 1 })
    await db.uploads.delete(score.id)
  })
}
