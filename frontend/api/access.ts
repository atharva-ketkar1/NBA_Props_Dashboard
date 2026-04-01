import { issueArchiveAccessToken, issuePlayerAccessToken, getOrCreateSession, parseSeason } from './_lib/access.js';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  parseInteger,
  rejectBrowserNavigation,
  rejectCrossSiteBrowserRequest,
  rejectUnknownAppClient,
} from './_lib/http.js';

export const runtime = 'nodejs';
export const maxDuration = 60;

async function handleGet(request: Request) {
  const crossSiteResponse = rejectCrossSiteBrowserRequest(request);
  if (crossSiteResponse) {
    return crossSiteResponse;
  }

  const navigationResponse = rejectBrowserNavigation(request);
  if (navigationResponse) {
    return navigationResponse;
  }

  const clientResponse = rejectUnknownAppClient(request);
  if (clientResponse) {
    return clientResponse;
  }

  const rateLimitResponse = enforceRateLimit(request, {
    bucket: 'access',
    limit: 160,
    windowMs: 60_000,
  });
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const requestUrl = new URL(request.url);
  const playerId = parseInteger(requestUrl.searchParams.get('playerId'));
  const archiveSeason = parseSeason(requestUrl.searchParams.get('archiveSeason'));

  if (!playerId) {
    return errorResponse(400, 'A valid playerId is required.');
  }

  try {
    const { sessionCookie, sessionId } = getOrCreateSession(request);
    const playerAccess = issuePlayerAccessToken(sessionId, playerId);
    const archiveAccess = archiveSeason
      ? issueArchiveAccessToken(sessionId, playerId, archiveSeason)
      : null;

    return jsonResponse({
      archiveToken: archiveAccess?.token ?? null,
      expiresAt: Math.min(
        playerAccess.expiresAt,
        archiveAccess?.expiresAt ?? Number.MAX_SAFE_INTEGER,
      ),
      playerToken: playerAccess.token,
    }, {
      headers: sessionCookie ? { 'Set-Cookie': sessionCookie } : undefined,
    });
  } catch (error) {
    console.error('[api/access]', error);
    return errorResponse(500, 'Failed to issue access tokens.');
  }
}

export const GET = handleGet;

export default {
  async fetch(request: Request) {
    if (request.method !== 'GET') {
      return methodNotAllowed();
    }

    return handleGet(request);
  },
};
