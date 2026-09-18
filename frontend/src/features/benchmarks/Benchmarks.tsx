import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getBenchmarks } from '../../lib/api'
import type {
  BenchmarkResults,
  DetectionMetrics,
  FullPieceResult,
  SampleVariantResult,
  TrackingMetrics,
} from '../../lib/benchmarkTypes'

// The method the live app actually runs (see backend/app/routers/audio_ws.py).
const LIVE_METHOD = 'markov-filter'
// Kept alongside it as a reference point: what the frontend used before
// tracking moved server-side (highest detected note -> nearest onset).
const BASELINE_METHOD = 'melody-pitch'

function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(digits)}%`
}

function frac(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : value.toFixed(digits)
}

function titleCase(slug: string): string {
  return slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function DetectionCell({ detection }: { detection?: DetectionMetrics }) {
  if (!detection) return <td>—</td>
  return (
    <td>
      <div>{pct(detection.exactPitch.f1 * 100)} exact</div>
      <div className="bench-subtle">{pct(detection.pitchClass.f1 * 100)} pitch-class</div>
    </td>
  )
}

function TrackingCell({ tracking, method }: { tracking?: TrackingMetrics; method: string }) {
  const m = tracking?.[method]
  if (!m) return <td>—</td>
  return (
    <td>
      <div>{pct(m.within3)} within ±3</div>
      <div className="bench-subtle">MAE {frac(m.meanAbsoluteError)}</div>
    </td>
  )
}

function FullPieceRow({ result }: { result: FullPieceResult }) {
  if (!result.hasPerformance) {
    return (
      <tr>
        <td>{titleCase(result.slug)}</td>
        <td colSpan={5} className="bench-subtle">
          sheet only -- no accompanying recording
        </td>
      </tr>
    )
  }
  if (result.error) {
    return (
      <tr>
        <td>{titleCase(result.slug)}</td>
        <td colSpan={5} className="bench-subtle">
          {result.error}
        </td>
      </tr>
    )
  }
  const live = result.livePath?.playthrough
  return (
    <tr>
      <td>{titleCase(result.slug)}</td>
      <td>
        {result.durationSeconds ? `${Math.round(result.durationSeconds)}s` : '—'}
        <div className="bench-subtle">
          {result.matchedOnsets}/{result.totalScoreOnsets} onsets matched
        </div>
      </td>
      <DetectionCell detection={result.detection} />
      <TrackingCell tracking={result.tracking} method={BASELINE_METHOD} />
      <TrackingCell tracking={result.tracking} method={LIVE_METHOD} />
      <td>
        {live ? (
          <>
            <div>largest move {live.largestSingleFrameMove} onsets</div>
            <div className="bench-subtle">
              MAE {frac(live.meanAbsoluteError)} · {pct(live.within3)} within ±3
            </div>
          </>
        ) : (
          '—'
        )}
      </td>
    </tr>
  )
}

function SampleVariantRow({ variant }: { variant: SampleVariantResult }) {
  return (
    <tr>
      <td>{titleCase(variant.variant)}</td>
      <td>{Math.round(variant.durationSeconds)}s</td>
      <DetectionCell detection={variant.detection} />
      <TrackingCell tracking={variant.tracking} method={BASELINE_METHOD} />
      <TrackingCell tracking={variant.tracking} method={LIVE_METHOD} />
    </tr>
  )
}

export function Benchmarks() {
  const [results, setResults] = useState<BenchmarkResults | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getBenchmarks()
      .then(setResults)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load benchmarks'))
  }, [])

  return (
    <div className="benchmarks">
      <header className="viewer-header">
        <h1>Accuracy Benchmarks</h1>
        <div className="viewer-header-actions">
          <Link className="btn btn-ghost" to="/">
            ← Back to library
          </Link>
        </div>
      </header>

      {error && (
        <p role="alert" className="viewer-alert">
          {error}. Run <code>scripts/run_benchmarks.py</code> in <code>backend/</code> to generate results.
        </p>
      )}
      {!results && !error && <p>Loading benchmarks…</p>}

      {results && (
        <>
          <p className="bench-generated">
            Generated {new Date(results.generatedAt).toLocaleString()}. Detection/tracking columns compare the
            live app's tracker (<strong>{titleCase(LIVE_METHOD)}</strong>) against the naive one it replaced (
            {titleCase(BASELINE_METHOD)}).
          </p>

          <section>
            <h2>Full sheet vs. real recording</h2>
            <p className="bench-subtle">
              One page per piece (OMR'd), scored against its actual accompanying performance. Ground truth is
              recovered by aligning detected audio onsets to the score -- a diagnostic aid, not exact.
            </p>
            <div className="bench-table-wrap">
              <table className="bench-table">
                <thead>
                  <tr>
                    <th>Piece</th>
                    <th>Recording</th>
                    <th>Note detection F1</th>
                    <th>{titleCase(BASELINE_METHOD)}</th>
                    <th>{titleCase(LIVE_METHOD)} (live)</th>
                    <th>Live path smoothness</th>
                  </tr>
                </thead>
                <tbody>
                  {results.full.map((r) => (
                    <FullPieceRow key={r.slug} result={r} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2>Synthesized sample tests</h2>
            <p className="bench-subtle">
              Short flawed-performance variants rendered from each score, whose true position is known exactly at
              every instant -- the same method scripts/evaluate_tracking.py uses.
            </p>
            {results.samples.map((piece) => (
              <details key={piece.slug} className="bench-piece" open={results.samples.length === 1}>
                <summary>
                  {titleCase(piece.slug)} <span className="bench-subtle">({piece.variants.length} variants)</span>
                </summary>
                <div className="bench-table-wrap">
                  <table className="bench-table">
                    <thead>
                      <tr>
                        <th>Variant</th>
                        <th>Length</th>
                        <th>Note detection F1</th>
                        <th>{titleCase(BASELINE_METHOD)}</th>
                        <th>{titleCase(LIVE_METHOD)} (live)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {piece.variants.map((v) => (
                        <SampleVariantRow key={v.variant} variant={v} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </section>
        </>
      )}
    </div>
  )
}
