import { fetchGamesPayload } from './_lib/dashboardData.js';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  parseIsoDateList,
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
      bucket: 'games',
      limit: 120,
      windowMs: 60_000,
    });
    if (rateLimitResponse) {
      return rateLimitResponse;
    }

    const requestUrl = new URL(request.url);
    const dates = parseIsoDateList(requestUrl.searchParams.get('dates'), 14);

    if (!dates.length) {
      return errorResponse(400, 'At least one valid date is required.');
    }

    try {
      const payload = await fetchGamesPayload(dates);
      return jsonResponse(payload, {
        cache: {
          browserMaxAge: 0,
          sMaxAge: 30,
          staleWhileRevalidate: 120,
        },
      });
    } catch (error) {
      console.error('[api/games]', error);
      return errorResponse(500, 'Failed to load schedule data.');
    }
  },
};
