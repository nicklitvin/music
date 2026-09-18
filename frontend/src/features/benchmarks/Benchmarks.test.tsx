import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Benchmarks } from './Benchmarks'
import type { BenchmarkResults } from '../../lib/benchmarkTypes'

function jsonResponse(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 404, statusText: 'x', json: async () => body } as Response
}

const SAMPLE_RESULTS: BenchmarkResults = {
  generatedAt: '2026-01-01T00:00:00',
  full: [
    {
      slug: 'aliez',
      hasPerformance: true,
      durationSeconds: 291,
      matchedOnsets: 353,
      totalScoreOnsets: 353,
      detection: {
        scoredFrames: 920,
        exactPitch: { precision: 0.3, recall: 0.6, f1: 0.4 },
        pitchClass: { precision: 0.5, recall: 0.8, f1: 0.6 },
        octaveErrorShareOfFalsePositives: 0.4,
        highestNoteExact: 0.23,
        highestNotePitchClass: 0.37,
      },
      tracking: {
        'melody-pitch': { exact: 8, within1: 8, within2: 9, within3: 9, meanAbsoluteError: 158 },
        'markov-filter': { exact: 26, within1: 54, within2: 68, within3: 75, meanAbsoluteError: 8.8 },
      },
      livePath: {
        playthrough: {
          finalOnset: 348,
          totalOnsets: 353,
          largestSingleFrameMove: 3,
          movesOverThreeOnsets: 0,
          meanAbsoluteError: 2.8,
          within3: 76,
          maxError: 15,
        },
        coldStarts: [],
      },
    },
    { slug: 'miiro', hasPerformance: false },
  ],
  samples: [
    {
      slug: 'aliez',
      variants: [
        {
          variant: 'clean',
          durationSeconds: 103,
          totalScoreOnsets: 353,
          detection: {
            scoredFrames: 1341,
            exactPitch: { precision: 0.4, recall: 0.8, f1: 0.54 },
            pitchClass: { precision: 0.57, recall: 0.86, f1: 0.69 },
            octaveErrorShareOfFalsePositives: 0.51,
            highestNoteExact: 0.33,
            highestNotePitchClass: 0.71,
          },
          tracking: {
            'melody-pitch': { exact: 0.3, within1: 0.6, within2: 0.9, within3: 1, meanAbsoluteError: 162 },
            'markov-filter': { exact: 93, within1: 99, within2: 100, within3: 100, meanAbsoluteError: 0.07 },
          },
        },
      ],
    },
  ],
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Benchmarks', () => {
  it('renders full-piece and sample rows once results load', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(SAMPLE_RESULTS)))

    render(
      <BrowserRouter>
        <Benchmarks />
      </BrowserRouter>,
    )

    expect((await screen.findAllByText('Aliez')).length).toBeGreaterThan(0)
    expect(screen.getByText(/sheet only/i)).toBeInTheDocument()
    expect(screen.getByText(/76% within/)).toBeInTheDocument()
  })

  it('shows an error state when the endpoint is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, false)))

    render(
      <BrowserRouter>
        <Benchmarks />
      </BrowserRouter>,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(/benchmarks unavailable/i)
  })
})
