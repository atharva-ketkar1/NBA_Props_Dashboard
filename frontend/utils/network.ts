const DEFAULT_API_BASE_URL = 'http://localhost:5000';
const DEFAULT_TIMEOUT_MS = 12_000;
const MAX_CONCURRENT_REQUESTS = 4;
const MAX_REQUESTS_PER_WINDOW = 10;
const RATE_WINDOW_MS = 1_000;

type GuardedFetchOptions = {
  allowedOrigins?: Array<string | null | undefined>;
  timeoutMs?: number;
  dedupe?: boolean;
};

const inflightRequests = new Map<string, Promise<Response>>();
const originRequestBuckets = new Map<string, number[]>();
const concurrencyWaiters: Array<() => void> = [];
let activeRequests = 0;

function getRuntimeOrigin() {
  if (typeof window !== 'undefined' && window.location.origin) {
    return window.location.origin;
  }

  return 'http://localhost';
}

function isLoopbackHost(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
}

function normalizeConfiguredUrl(rawValue: string | undefined, label: string) {
  const trimmed = rawValue?.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = new URL(trimmed);
    if (!['http:', 'https:'].includes(parsed.protocol)) {
      throw new Error(`Unsupported protocol: ${parsed.protocol}`);
    }

    if (parsed.protocol === 'http:' && !isLoopbackHost(parsed.hostname)) {
      console.warn(`[security] ${label} should use HTTPS outside local development.`);
    }

    return parsed.href.replace(/\/$/, '');
  } catch (error) {
    console.warn(`[security] Ignoring invalid ${label}.`, error);
    return null;
  }
}

function normalizeOrigin(rawValue: string | null | undefined) {
  if (!rawValue) {
    return null;
  }

  try {
    return new URL(rawValue).origin;
  } catch {
    return null;
  }
}

function resolveRequestUrl(input: RequestInfo | URL) {
  if (input instanceof URL) {
    return new URL(input.toString());
  }

  if (typeof Request !== 'undefined' && input instanceof Request) {
    return new URL(input.url, getRuntimeOrigin());
  }

  return new URL(String(input), getRuntimeOrigin());
}

function getRequestMethod(input: RequestInfo | URL, init?: RequestInit) {
  if (init?.method) {
    return init.method.toUpperCase();
  }

  if (typeof Request !== 'undefined' && input instanceof Request) {
    return input.method.toUpperCase();
  }

  return 'GET';
}

function isIdempotentMethod(method: string) {
  return method === 'GET' || method === 'HEAD';
}

function buildAllowedOrigins(extraOrigins: Array<string | null | undefined> = []) {
  const origins = new Set<string>([getRuntimeOrigin()]);

  extraOrigins.forEach((origin) => {
    const normalized = normalizeOrigin(origin);
    if (normalized) {
      origins.add(normalized);
    }
  });

  return origins;
}

function assertAllowedRequest(url: URL, allowedOrigins: Set<string>) {
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error(`Blocked unsupported protocol: ${url.protocol}`);
  }

  if (!allowedOrigins.has(url.origin)) {
    throw new Error(`Blocked request to unapproved origin: ${url.origin}`);
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });
}

async function acquireConcurrencySlot() {
  if (activeRequests < MAX_CONCURRENT_REQUESTS) {
    activeRequests += 1;
    return;
  }

  await new Promise<void>((resolve) => {
    concurrencyWaiters.push(resolve);
  });

  activeRequests += 1;
}

function releaseConcurrencySlot() {
  activeRequests = Math.max(0, activeRequests - 1);
  const next = concurrencyWaiters.shift();
  if (next) {
    next();
  }
}

async function enforceRateLimit(origin: string) {
  while (true) {
    const now = Date.now();
    const recentRequests = (originRequestBuckets.get(origin) ?? []).filter(
      (timestamp) => now - timestamp < RATE_WINDOW_MS,
    );

    if (recentRequests.length < MAX_REQUESTS_PER_WINDOW) {
      recentRequests.push(now);
      originRequestBuckets.set(origin, recentRequests);
      return;
    }

    originRequestBuckets.set(origin, recentRequests);
    const waitMs = Math.max(50, RATE_WINDOW_MS - (now - recentRequests[0]) + 10);
    await sleep(waitMs);
  }
}

function createAbortSignal(timeoutMs: number, externalSignal?: AbortSignal | null) {
  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => {
    controller.abort(new DOMException('Request timed out.', 'TimeoutError'));
  }, timeoutMs);

  const onAbort = () => {
    controller.abort(externalSignal?.reason ?? new DOMException('Request aborted.', 'AbortError'));
  };

  if (externalSignal) {
    if (externalSignal.aborted) {
      onAbort();
    } else {
      externalSignal.addEventListener('abort', onAbort, { once: true });
    }
  }

  return {
    signal: controller.signal,
    cleanup: () => {
      globalThis.clearTimeout(timeoutId);
      externalSignal?.removeEventListener('abort', onAbort);
    },
  };
}

async function performFetch(url: URL, init: RequestInit | undefined, timeoutMs: number) {
  await acquireConcurrencySlot();

  try {
    await enforceRateLimit(url.origin);

    const { signal, cleanup } = createAbortSignal(timeoutMs, init?.signal ?? null);
    try {
      return await fetch(url.toString(), {
        ...init,
        signal,
        credentials: init?.credentials ?? 'omit',
        referrerPolicy: init?.referrerPolicy ?? 'strict-origin-when-cross-origin',
      });
    } finally {
      cleanup();
    }
  } finally {
    releaseConcurrencySlot();
  }
}

const API_BASE_URL = normalizeConfiguredUrl(import.meta.env.VITE_API_BASE_URL, 'VITE_API_BASE_URL') ?? DEFAULT_API_BASE_URL;
const SUPABASE_URL = normalizeConfiguredUrl(import.meta.env.VITE_SUPABASE_URL, 'VITE_SUPABASE_URL');

export function getApiBaseUrl() {
  return API_BASE_URL;
}

export function getSupabaseOrigin() {
  return SUPABASE_URL ? new URL(SUPABASE_URL).origin : null;
}

export function buildApiUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return new URL(normalizedPath, `${API_BASE_URL}/`).toString();
}

export async function guardedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
  options: GuardedFetchOptions = {},
) {
  const url = resolveRequestUrl(input);
  const method = getRequestMethod(input, init);
  const allowedOrigins = buildAllowedOrigins(options.allowedOrigins);
  const shouldDedupe = options.dedupe !== false && isIdempotentMethod(method);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  assertAllowedRequest(url, allowedOrigins);

  if (!shouldDedupe) {
    return performFetch(url, init, timeoutMs);
  }

  const requestKey = `${method} ${url.toString()}`;
  let requestPromise = inflightRequests.get(requestKey);

  if (!requestPromise) {
    requestPromise = performFetch(url, init, timeoutMs);
    inflightRequests.set(requestKey, requestPromise);
    requestPromise.finally(() => {
      inflightRequests.delete(requestKey);
    });
  }

  const response = await requestPromise;
  return response.clone();
}

export async function fetchJson<T>(
  input: RequestInfo | URL,
  init?: RequestInit,
  options?: GuardedFetchOptions,
) {
  const response = await guardedFetch(input, init, options);
  if (!response.ok) {
    throw new Error(`[network] ${response.status} ${response.statusText} for ${response.url || String(input)}`);
  }

  return response.json() as Promise<T>;
}

export function fetchApiJson<T>(path: string, init?: RequestInit) {
  return fetchJson<T>(buildApiUrl(path), init, {
    allowedOrigins: [API_BASE_URL],
    dedupe: true,
  });
}
