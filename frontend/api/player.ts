import { fetchPlayerPayload } from './_lib/dashboardData.js';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  parseInteger,
  rejectCrossSiteBrowserRequest,
} from './_lib/http.js';

export const runtime = 'nodejs';
export const maxDuration = 10;

export default {
  async fetch(request: Request) {
    if (request.method !== 'GET') {
      return methodNotAllowed();
    }

    const crossSiteResponse = rejectCrossSiteBrowserRequest(request);
    if (crossSiteResponse) {
      return crossSiteResponse;
    }

    const rateLimitResponse = enforceRateLimit(request, {
      bucket: 'player',
      limit: 90,
      windowMs: 60_000,
    });
    if (rateLimitResponse) {
      return rateLimitResponse;
    }

    const requestUrl = new URL(request.url);
    const playerId = parseInteger(requestUrl.searchParams.get('playerId'));

    if (!playerId) {
      return errorResponse(400, 'A valid playerId is required.');
    }

    try {
      const payload = await fetchPlayerPayload(playerId);
      return jsonResponse(payload, {
        cache: {
          browserMaxAge: 0,
          sMaxAge: 300,
          staleWhileRevalidate: 1800,
        },
      });
    } catch (error) {
      console.error('[api/player]', error);
      return errorResponse(500, 'Failed to load player details.');
    }
  },
};
