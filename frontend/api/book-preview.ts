import { fetchPlayerSportsbookPreviewPayload } from './_lib/dashboardData.js';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  parseInteger,
  parseIsoDate,
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
    bucket: 'book-preview',
    limit: 180,
    windowMs: 60_000,
  });
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const requestUrl = new URL(request.url);
  const playerId = parseInteger(requestUrl.searchParams.get('playerId'));
  const statType = requestUrl.searchParams.get('statType')?.trim() ?? '';
  const gameDate = parseIsoDate(requestUrl.searchParams.get('gameDate'));

  if (!playerId || !statType) {
    return errorResponse(400, 'A valid playerId and statType are required.');
  }

  try {
    const payload = await fetchPlayerSportsbookPreviewPayload({
      playerId,
      statType,
      gameDate,
    });

    return jsonResponse(payload, {
      cache: {
        browserMaxAge: 0,
        sMaxAge: 15,
        staleWhileRevalidate: 60,
      },
    });
  } catch (error) {
    console.error('[api/book-preview]', error);
    return errorResponse(500, 'Failed to load sportsbook preview lines.');
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
