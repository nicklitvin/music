/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// Stamped by vite.config.ts at build time; absent under vitest, which is
// why src/lib/version.ts guards for it.
declare const __BUILD_DATE__: string | undefined
