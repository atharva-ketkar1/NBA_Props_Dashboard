import { fetchSeasonSplitsPayload } from './_lib/dashboardData.js';
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

/** NBA-format season strings only, e.g. 2025-26. Nothing else reaches the query. */
const SEASON_PATTERN = /^\d{4}-\d{2}$/;
const TRICODE_PATTERN = /^[A-Z]{3}$/;

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
    bucket: 'season-splits',
    limit: 60,
    windowMs: 60_000,
  });
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const requestUrl = new URL(request.url);
  const playerId = parseInteger(requestUrl.searchParams.get('playerId'));
  const season = (requestUrl.searchParams.get('season') ?? '').trim();
  const rawOpponent = (requestUrl.searchParams.get('opponent') ?? '').trim().toUpperCase();

  if (!playerId) {
    return errorResponse(400, 'A valid playerId is required.');
  }

  if (!SEASON_PATTERN.test(season)) {
    return errorResponse(400, 'A valid season is required.');
  }

  // An unknown opponent is not an error -- the panel still has a player profile
  // to show, it just cannot colour it against a defense.
  const opponent = TRICODE_PATTERN.test(rawOpponent) ? rawOpponent : null;

  try {
    const payload = await fetchSeasonSplitsPayload(playerId, season, opponent);

    return jsonResponse(payload, {
      cache: {
        browserMaxAge: 0,
        sMaxAge: 300,
        staleWhileRevalidate: 3600,
      },
    });
  } catch (error) {
    console.error('[api/season-splits]', error);
    return errorResponse(500, 'Failed to load season splits.');
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
