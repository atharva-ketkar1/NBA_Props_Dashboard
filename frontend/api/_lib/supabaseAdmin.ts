import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createClient } from '@supabase/supabase-js';

declare global {
  // eslint-disable-next-line no-var
  var __propsmadnessSupabaseAdmin:
    | ReturnType<typeof createClient>
    | undefined;
  // eslint-disable-next-line no-var
  var __propsmadnessLocalEnvCache:
    | Map<string, string>
    | undefined;
}

const LOCAL_ENV_FILENAMES = [
  '.env.local',
  '.env',
  path.join('.vercel', '.env.development.local'),
];
const currentFilePath = fileURLToPath(import.meta.url);
const currentDir = path.dirname(currentFilePath);

function stripQuotes(value: string) {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
    || (trimmed.startsWith('\'') && trimmed.endsWith('\''))
  ) {
    return trimmed.slice(1, -1);
  }

  return trimmed;
}

function parseEnvFile(filePath: string) {
  const envMap = new Map<string, string>();

  if (!fs.existsSync(filePath)) {
    return envMap;
  }

  for (const rawLine of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }

    const separatorIndex = line.indexOf('=');
    if (separatorIndex <= 0) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    const value = stripQuotes(line.slice(separatorIndex + 1));
    if (!key || !value) {
      continue;
    }

    envMap.set(key, value);
  }

  return envMap;
}

function loadLocalEnvFallback() {
  if (!globalThis.__propsmadnessLocalEnvCache) {
    const searchRoots = [
      process.cwd(),
      path.resolve(process.cwd(), 'frontend'),
      path.resolve(currentDir, '..', '..'),
      path.resolve(currentDir, '..', '..', '..'),
    ];

    const mergedEnv = new Map<string, string>();

    for (const root of searchRoots) {
      for (const filename of LOCAL_ENV_FILENAMES) {
        const filePath = path.join(root, filename);
        const fileEnv = parseEnvFile(filePath);
        for (const [key, value] of fileEnv) {
          if (!mergedEnv.has(key)) {
            mergedEnv.set(key, value);
          }
        }
      }
    }

    globalThis.__propsmadnessLocalEnvCache = mergedEnv;
  }

  return globalThis.__propsmadnessLocalEnvCache;
}

export function getOptionalEnv(name: string) {
  const runtimeValue = process.env[name]?.trim();
  if (runtimeValue) {
    return runtimeValue;
  }

  return loadLocalEnvFallback().get(name)?.trim() || '';
}

export function getSupabaseAdmin() {
  if (!globalThis.__propsmadnessSupabaseAdmin) {
    const url = getOptionalEnv('SUPABASE_URL') || getOptionalEnv('VITE_SUPABASE_URL');
    const key = getOptionalEnv('SUPABASE_SERVICE_ROLE_KEY')
      || getOptionalEnv('SUPABASE_SERVICE_KEY')
      || getOptionalEnv('SUPABASE_SECRET_KEY')
      || getOptionalEnv('VITE_SUPABASE_ANON_KEY');

    if (!url) {
      throw new Error('Missing SUPABASE_URL or VITE_SUPABASE_URL.');
    }

    if (!key) {
      throw new Error('Missing SUPABASE_SECRET_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_SERVICE_KEY, or VITE_SUPABASE_ANON_KEY.');
    }

    globalThis.__propsmadnessSupabaseAdmin = createClient(url, key, {
      auth: {
        autoRefreshToken: false,
        detectSessionInUrl: false,
        persistSession: false,
      },
    });
  }

  return globalThis.__propsmadnessSupabaseAdmin;
}
