import { LATEST_PATCH_NOTE } from './patchNotes'

// Stamped at build time by vite.config.ts, so what the chip shows is the
// date of the build actually being served -- which is the question you are
// asking when you look at it after a deploy ("did my change go out?").
//
// In dev there is no stamp, so it falls back to the newest patch note.
export const BUILD_DATE: string =
  typeof __BUILD_DATE__ === 'string' && __BUILD_DATE__ ? __BUILD_DATE__ : LATEST_PATCH_NOTE.date
