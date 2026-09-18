// Mirrors backend/app/services/benchmark_eval.py's output shapes and
// backend/benchmark_results.json, produced by backend/scripts/run_benchmarks.py.

export interface PrecisionRecallF1 {
  precision: number
  recall: number
  f1: number
}

export interface DetectionMetrics {
  scoredFrames: number
  exactPitch: PrecisionRecallF1
  pitchClass: PrecisionRecallF1
  octaveErrorShareOfFalsePositives: number | null
  highestNoteExact: number | null
  highestNotePitchClass: number | null
}

export interface TrackingMethodMetrics {
  exact: number
  within1: number
  within2: number
  within3: number
  meanAbsoluteError: number
}

// Keyed by tracker name (melody-pitch, chroma-template, salience-template,
// hmm-salience, markov-filter) -- see backend/app/services/position_tracking.py.
export type TrackingMetrics = Record<string, TrackingMethodMetrics | null>

export interface ColdStart {
  startSeconds: number
  locksAfterSeconds: number | null
  largestMoveAfterLock: number | null
}

export interface LivePathMetrics {
  playthrough: {
    finalOnset: number
    totalOnsets: number
    largestSingleFrameMove: number
    movesOverThreeOnsets: number
    meanAbsoluteError: number | null
    within3: number | null
    maxError: number | null
  }
  coldStarts: ColdStart[]
}

export interface FullPieceResult {
  slug: string
  hasPerformance: boolean
  error?: string
  durationSeconds?: number
  detectedOnsets?: number
  matchedOnsets?: number
  totalScoreOnsets?: number
  lastMatchedTimeSeconds?: number
  detection?: DetectionMetrics
  tracking?: TrackingMetrics
  livePath?: LivePathMetrics
  warnings?: string[]
}

export interface SampleVariantResult {
  variant: string
  durationSeconds: number
  totalScoreOnsets: number
  detection: DetectionMetrics
  tracking: TrackingMetrics
}

export interface SamplePieceResult {
  slug: string
  variants: SampleVariantResult[]
}

export interface BenchmarkResults {
  generatedAt: string
  full: FullPieceResult[]
  samples: SamplePieceResult[]
}
