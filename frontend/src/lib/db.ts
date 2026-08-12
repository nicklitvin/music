import Dexie, { type EntityTable } from 'dexie'
import type { ScoreRecord } from './types'

class ScoreDatabase extends Dexie {
  scores!: EntityTable<ScoreRecord, 'id'>

  constructor() {
    super('sheet-music-tracker')
    this.version(1).stores({
      scores: 'id, title, uploadDate',
    })
  }
}

export const db = new ScoreDatabase()

export async function listScores(): Promise<ScoreRecord[]> {
  return db.scores.orderBy('uploadDate').reverse().toArray()
}

export async function getScore(id: string): Promise<ScoreRecord | undefined> {
  return db.scores.get(id)
}

export async function saveScore(score: ScoreRecord): Promise<void> {
  await db.scores.put(score)
}

export async function deleteScore(id: string): Promise<void> {
  await db.scores.delete(id)
}
