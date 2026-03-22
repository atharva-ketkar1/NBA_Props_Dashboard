import { createClient } from '@supabase/supabase-js';
import { getSupabaseOrigin, guardedFetch } from './network';

const url = import.meta.env.VITE_SUPABASE_URL as string;
const key = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

if (!url || !key) {
  console.warn(
    '[supabase] VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY is not set. ' +
    'Set VITE_USE_DB=false to use the local JSON feed instead.'
  );
}

const allowedOrigins = [getSupabaseOrigin()].filter(Boolean) as string[];

export const supabase = createClient(url ?? '', key ?? '', {
  auth: {
    autoRefreshToken: false,
    detectSessionInUrl: false,
    persistSession: false,
  },
  global: {
    fetch: (input, init) => guardedFetch(input, init, {
      allowedOrigins,
      dedupe: true,
      timeoutMs: 12_000,
    }),
  },
});
