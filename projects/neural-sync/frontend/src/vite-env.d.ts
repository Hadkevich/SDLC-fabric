/// <reference types="vite/client" />

/**
 * Augment ImportMeta so that import.meta.env.VITE_API_BASE_URL is typed
 * even before `npm install` resolves vite's full declaration file.
 * Once vite/client is installed, these declarations are merged.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
