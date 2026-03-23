import { fetchSimilarCandidatesPayload } from './_lib/dashboardData.js';
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
export const maxDuration = 10;

const SUPPORTED_SPORTSBOOKS = new Set(['dk', 'fd', 'mgm', 'cz']);

export default {
  async fetch(request: Request) {
    if (request.method !== 'GET') {
      return methodNotAllowed();
    }

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
      bucket: 'similar',
      limit: 20,
      windowMs: 60_000,
    });
    if (rateLimitResponse) {
      return rateLimitResponse;
    }

    const requestUrl = new URL(request.url);
    const playerId = parseInteger(requestUrl.searchParams.get('playerId'));
    const selectedGameDate = parseIsoDate(requestUrl.searchParams.get('selectedGameDate'));
    const activeTab = (requestUrl.searchParams.get('activeTab') ?? 'Points').slice(0, 64);
    const activeSportsbook = requestUrl.searchParams.get('activeSportsbook') ?? 'dk';

    if (!playerId) {
      return errorResponse(400, 'A valid playerId is required.');
    }

    if (!SUPPORTED_SPORTSBOOKS.has(activeSportsbook)) {
      return errorResponse(400, 'A valid sportsbook is required.');
    }

    try {
      const payload = await fetchSimilarCandidatesPayload({
        activeSportsbook: activeSportsbook as 'dk' | 'fd' | 'mgm' | 'cz',
        activeTab,
        playerId,
        selectedGameDate,
      });

      return jsonResponse(payload, {
        cache: {
          browserMaxAge: 0,
          sMaxAge: 30,
          staleWhileRevalidate: 120,
        },
      });
    } catch (error) {
      console.error('[api/similar]', error);
      return errorResponse(500, 'Failed to rank similar players.');
    }
  },
};
