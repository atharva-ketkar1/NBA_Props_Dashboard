import { createHmac, randomBytes, timingSafeEqual } from 'crypto';
import { getOptionalEnv } from './supabaseAdmin.js';

type AccessScope = 'player' | 'archive';

type AccessPayload = {
  exp: number;
  playerId: number;
  scope: AccessScope;
  season?: string;
  sessionId: string;
  v: 1;
};

const SESSION_COOKIE_NAME = 'propx_sid';
const SESSION_MAX_AGE_SECONDS = 60 * 60 * 12;
const TOKEN_TTL_MS = 2 * 60_000;
const SESSION_ID_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;
const SEASON_PATTERN = /^\d{4}-\d{2}$/;

function parseCookies(request: Request) {
  const cookieHeader = request.headers.get('cookie') ?? '';
  const cookies = new Map<string, string>();

  cookieHeader.split(';').forEach((part) => {
    const separatorIndex = part.indexOf('=');
    if (separatorIndex <= 0) {
      return;
    }

    const key = part.slice(0, separatorIndex).trim();
    const value = part.slice(separatorIndex + 1).trim();
    if (key && value) {
      cookies.set(key, value);
    }
  });

  return cookies;
}

function getSigningSecret() {
  const secret = getOptionalEnv('API_SIGNING_SECRET')
    || getOptionalEnv('SUPABASE_SECRET_KEY')
    || getOptionalEnv('SUPABASE_SERVICE_ROLE_KEY');

  if (!secret) {
    throw new Error('Missing API signing secret.');
  }

  return secret;
}

function signPayload(encodedPayload: string) {
  return createHmac('sha256', getSigningSecret())
    .update(encodedPayload)
    .digest('base64url');
}

function encodePayload(payload: AccessPayload) {
  return Buffer.from(JSON.stringify(payload)).toString('base64url');
}

function decodePayload(encodedPayload: string) {
  const raw = Buffer.from(encodedPayload, 'base64url').toString('utf8');
  return JSON.parse(raw) as AccessPayload;
}

function isValidSessionId(value: string | undefined | null): value is string {
  return !!value && SESSION_ID_PATTERN.test(value);
}

function buildSessionCookie(request: Request, sessionId: string) {
  const secure = new URL(request.url).protocol === 'https:';
  return [
    `${SESSION_COOKIE_NAME}=${sessionId}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    `Max-Age=${SESSION_MAX_AGE_SECONDS}`,
    secure ? 'Secure' : '',
  ].filter(Boolean).join('; ');
}

function buildToken(payload: Omit<AccessPayload, 'exp' | 'v'>) {
  const exp = Date.now() + TOKEN_TTL_MS;
  const tokenPayload: AccessPayload = {
    ...payload,
    exp,
    v: 1,
  };
  const encodedPayload = encodePayload(tokenPayload);
  const signature = signPayload(encodedPayload);

  return {
    expiresAt: exp,
    token: `${encodedPayload}.${signature}`,
  };
}

function verifyToken(request: Request, token: string, scope: AccessScope) {
  const [encodedPayload, signature] = token.split('.');
  if (!encodedPayload || !signature) {
    return null;
  }

  const expectedSignature = signPayload(encodedPayload);
  const providedSignature = Buffer.from(signature);
  const computedSignature = Buffer.from(expectedSignature);

  if (
    providedSignature.length !== computedSignature.length
    || !timingSafeEqual(providedSignature, computedSignature)
  ) {
    return null;
  }

  let payload: AccessPayload;
  try {
    payload = decodePayload(encodedPayload);
  } catch {
    return null;
  }

  if (payload.v !== 1 || payload.scope !== scope || payload.exp <= Date.now()) {
    return null;
  }

  if (!Number.isInteger(payload.playerId) || payload.playerId <= 0) {
    return null;
  }

  if (!isValidSessionId(payload.sessionId)) {
    return null;
  }

  const sessionId = parseCookies(request).get(SESSION_COOKIE_NAME);
  if (!isValidSessionId(sessionId) || sessionId !== payload.sessionId) {
    return null;
  }

  if (scope === 'archive' && (!payload.season || !SEASON_PATTERN.test(payload.season))) {
    return null;
  }

  return payload;
}

export function getOrCreateSession(request: Request) {
  const existingSessionId = parseCookies(request).get(SESSION_COOKIE_NAME);
  if (isValidSessionId(existingSessionId)) {
    return {
      sessionCookie: null,
      sessionId: existingSessionId,
    };
  }

  const sessionId = randomBytes(18).toString('base64url');
  return {
    sessionCookie: buildSessionCookie(request, sessionId),
    sessionId,
  };
}

export function issuePlayerAccessToken(sessionId: string, playerId: number) {
  return buildToken({
    playerId,
    scope: 'player',
    sessionId,
  });
}

export function issueArchiveAccessToken(sessionId: string, playerId: number, season: string) {
  if (!SEASON_PATTERN.test(season)) {
    throw new Error('Invalid archive season.');
  }

  return buildToken({
    playerId,
    scope: 'archive',
    season,
    sessionId,
  });
}

export function parseSeason(value: string | null | undefined) {
  if (!value) {
    return null;
  }

  return SEASON_PATTERN.test(value) ? value : null;
}

export function readPlayerToken(request: Request, token: string) {
  const payload = verifyToken(request, token, 'player');
  return payload ? { playerId: payload.playerId } : null;
}

export function readArchiveToken(request: Request, token: string) {
  const payload = verifyToken(request, token, 'archive');
  return payload ? { playerId: payload.playerId, season: payload.season! } : null;
}
