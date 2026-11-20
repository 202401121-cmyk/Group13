/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the backend runs. Default http://localhost:8000. */
  readonly VITE_API_BASE_URL?: string;
  /** "mock" (default) or "live". */
  readonly VITE_API_MODE?: string;
  /** The Supabase project's address, for sign-in (US-30). Public. */
  readonly VITE_SUPABASE_URL?: string;
  /** The project's publishable key, sb_publishable_... (D-32). Public by design: never a secret key. */
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
