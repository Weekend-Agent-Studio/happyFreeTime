interface ImportMetaEnv {
  readonly VITE_PUBLIC_DEMO?: string;
  readonly VITE_SHOW_DEVELOPER_DETAILS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

