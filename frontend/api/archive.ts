import { fetchArchivePayload } from './_lib/dashboardData';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  parseInteger,
  rejectCrossSiteBrowserRequest,
} from './_lib/http';

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
      bucket: 'archive',
      limit: 60,
      windowMs: 60_000,
    });
    if (rateLimitResponse) {
      return rateLimitResponse;
    }

    const requestUrl = new URL(request.url);
    const playerId = parseInteger(requestUrl.searchParams.get('playerId'));
    const season = requestUrl.searchParams.get('season') ?? '2024-25';

    if (!playerId) {
      return errorResponse(400, 'A valid playerId is required.');
    }

    try {
      const payload = await fetchArchivePayload(playerId, season);
      return jsonResponse(payload, {
        cache: {
          browserMaxAge: 0,
          sMaxAge: 1800,
          staleWhileRevalidate: 86_400,
        },
      });
    } catch (error) {
      console.error('[api/archive]', error);
      return errorResponse(500, 'Failed to load archived game logs.');
    }
  },
};
