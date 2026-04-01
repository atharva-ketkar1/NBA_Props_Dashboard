type CacheProfile = {
  browserMaxAge?: number;
  sMaxAge?: number;
  staleWhileRevalidate?: number;
};

type JsonResponseOptions = {
  cache?: CacheProfile;
  headers?: HeadersInit;
  status?: number;
};

type RateLimitConfig = {
  bucket: string;
  limit: number;
  windowMs: number;
};

declare global {
  // eslint-disable-next-line no-var
  var __propsmadnessRateBuckets: Map<string, number[]> | undefined;
}

const rateBuckets = globalThis.__propsmadnessRateBuckets ??= new Map<string, number[]>();
const APP_CLIENT_HEADER_NAME = 'x-propx-client';
const APP_CLIENT_HEADER_VALUE = 'web';

function buildCacheControl(cache?: CacheProfile) {
  if (!cache) {
    return 'private, no-store';
  }

  const browserMaxAge = Math.max(0, Math.floor(cache.browserMaxAge ?? 0));
  const sMaxAge = Math.max(0, Math.floor(cache.sMaxAge ?? 0));
  const staleWhileRevalidate = Math.max(0, Math.floor(cache.staleWhileRevalidate ?? 0));

  return [
    'public',
    `max-age=${browserMaxAge}`,
    `s-maxage=${sMaxAge}`,
    `stale-while-revalidate=${staleWhileRevalidate}`,
  ].join(', ');
}

function buildBaseHeaders(cache?: CacheProfile, headers?: HeadersInit) {
  const responseHeaders = new Headers(headers);
  responseHeaders.set('Cache-Control', buildCacheControl(cache));
  responseHeaders.set('Content-Type', 'application/json; charset=utf-8');
  responseHeaders.set('Cross-Origin-Resource-Policy', 'same-origin');
  responseHeaders.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  responseHeaders.set('X-Content-Type-Options', 'nosniff');
  return responseHeaders;
}

export function jsonResponse(payload: unknown, options: JsonResponseOptions = {}) {
  return Response.json(payload, {
    status: options.status ?? 200,
    headers: buildBaseHeaders(options.cache, options.headers),
  });
}

export function errorResponse(status: number, error: string) {
  return jsonResponse({ error }, {
    status,
    cache: undefined,
  });
}

export function methodNotAllowed() {
  return errorResponse(405, 'Method not allowed.');
}

export function rejectCrossSiteBrowserRequest(request: Request) {
  const site = request.headers.get('sec-fetch-site');
  if (!site) {
    return null;
  }

  if (site === 'same-origin' || site === 'same-site' || site === 'none') {
    return null;
  }

  return errorResponse(403, 'Cross-site browser requests are not allowed.');
}

export function rejectBrowserNavigation(request: Request) {
  const mode = request.headers.get('sec-fetch-mode');
  const dest = request.headers.get('sec-fetch-dest');

  if (mode === 'navigate' || dest === 'document') {
    return errorResponse(404, 'Not found.');
  }

  return null;
}

export function rejectUnknownAppClient(request: Request) {
  if (request.headers.get(APP_CLIENT_HEADER_NAME) === APP_CLIENT_HEADER_VALUE) {
    return null;
  }

  return errorResponse(403, 'Unsupported client.');
}

function getClientIp(request: Request) {
  const forwardedFor = request.headers.get('x-forwarded-for');
  if (forwardedFor) {
    return forwardedFor.split(',')[0]?.trim() || 'unknown';
  }

  return request.headers.get('x-real-ip') || 'unknown';
}

export function enforceRateLimit(request: Request, config: RateLimitConfig) {
  const now = Date.now();
  const bucketKey = `${config.bucket}:${getClientIp(request)}`;
  const recentRequests = (rateBuckets.get(bucketKey) ?? []).filter(
    (timestamp) => now - timestamp < config.windowMs,
  );

  if (recentRequests.length >= config.limit) {
    rateBuckets.set(bucketKey, recentRequests);
    return errorResponse(429, 'Rate limit exceeded.');
  }

  recentRequests.push(now);
  rateBuckets.set(bucketKey, recentRequests);
  return null;
}

export function parseInteger(value: string | null, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!value) {
    return null;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    return null;
  }

  return parsed;
}

export function parseIsoDate(value: string | null) {
  if (!value) {
    return null;
  }

  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

export function parseIsoDateList(value: string | null, maxItems = 14) {
  if (!value) {
    return [];
  }

  const uniqueDates = Array.from(new Set(
    value
      .split(',')
      .map((part) => parseIsoDate(part.trim()))
      .filter(Boolean),
  )) as string[];

  return uniqueDates.slice(0, maxItems);
}
