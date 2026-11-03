/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the backend runs. Default http://localhost:8000. */
  readonly VITE_API_BASE_URL?: string;
  /** "mock" (default) or "live". */
  readonly VITE_API_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
