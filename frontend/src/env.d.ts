/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Bearer token for the agent API; unset in local dev. */
  readonly VITE_API_TOKEN?: string
}
