export interface PatchNote {
  /** ISO date, newest first. */
  date: string
  title: string
  changes: string[]
}

// Hand-written rather than generated from commits: a commit log says what
// changed in the code, which is not the same as what changed for someone
// using this. Add a new entry at the top when you ship something worth
// noticing.
export const PATCH_NOTES: PatchNote[] = [
  {
    date: '2026-10-08',
    title: 'Uploads that actually finish',
    changes: [
      'Fixed uploads that never finished: long sheets were being cut off partway through processing.',
      'Refreshing or closing the app no longer loses an upload — it picks up where it left off.',
      'Progress shows pages actually finished, and whether your sheet is waiting behind another.',
      'If the server is briefly unreachable the upload keeps retrying instead of failing.',
      'In-progress uploads can be cancelled, and failed ones retried.',
      'Fixed uploads on iPhone being rejected as invalid.',
    ],
  },
  {
    date: '2026-10-06',
    title: 'Keyboard and tap navigation',
    changes: [
      'Left and right arrows move a line; up and down move a page.',
      'On touch, tap the left or right edge of the sheet to move a line, or the middle (upper half / lower half) to move a page.',
      'Previous and Next have left the bottom bar now that the arrows and taps cover them.',
      'Fixed the highlight stretching from one system’s bass clef into the next system’s treble.',
      'Fixed following stalling: the sheet now moves when the music reaches a new line instead of waiting until it had drifted several lines away.',
      'Scrolling by hand now tells the tracker where you went, so it picks up from there instead of pulling you back.',
      'Access is behind a token.',
    ],
  },
  {
    date: '2026-10-01',
    title: 'Steadier tracking, continuous sheet',
    changes: [
      'The sheet is one continuous scroll instead of separate swiped pages.',
      'The line being played is highlighted and kept in the middle of the screen.',
      'Recording is opt-in — opening a sheet no longer switches your microphone on.',
      'The tracker stays still when nothing is being played, instead of wandering.',
      'It can no longer throw you across the score; large jumps are refused outright.',
      'Tracking is confined to the music actually on your screen, which is what stopped most of the jumping.',
      'Sheet recognition runs several pages at once, scaled to free memory, so uploads finish sooner.',
      'Icons throughout, a red delete button, upload as a sheet rather than its own page, and storage shown as a bar.',
    ],
  },
  {
    date: '2026-09-20',
    title: 'Better note detection',
    changes: [
      'Note detection now uses a learned transcription model rather than hand-written pitch analysis — the single biggest accuracy gain so far.',
      'Uploads show an estimated time remaining instead of an open-ended spinner, since recognition takes about five minutes per page.',
    ],
  },
  {
    date: '2026-09-18',
    title: 'Whole-sheet recognition',
    changes: [
      'Every page of a sheet is recognised, not just the first — tracking past the opening minute now works at all.',
      'Sheets whose recording is in a different key are detected and flagged rather than silently tracking badly.',
    ],
  },
  {
    date: '2026-09-17',
    title: 'Mobile-first redesign',
    changes: [
      'Rebuilt around a bottom pill bar, a swipeable sheet viewer and a drag-to-reorder library.',
      'Scores, page images and detected notes are stored only on your device.',
    ],
  },
]

export const LATEST_PATCH_NOTE = PATCH_NOTES[0]
