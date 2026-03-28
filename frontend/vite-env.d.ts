/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_ASSETS_URL?: string;
  readonly VITE_FULL_DB_POLL_MS?: string;
  readonly VITE_HOT_DATA_POLL_MS?: string;
  readonly VITE_USE_DB?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
