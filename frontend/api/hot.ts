import { fetchHotPayload } from './_lib/dashboardData.js';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  parseIsoDate,
  rejectBrowserNavigation,
  rejectCrossSiteBrowserRequest,
  rejectUnknownAppClient,
} from './_lib/http.js';

export const runtime = 'nodejs';
export const maxDuration = 60;
const SUPPORTED_SPORTSBOOKS = new Set(['dk', 'fd', 'mgm', 'cz', 'pp']);

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
    bucket: 'hot',
    limit: 120,
    windowMs: 60_000,
  });
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  const requestUrl = new URL(request.url);
  const selectedDate = parseIsoDate(requestUrl.searchParams.get('selectedDate'));
  const lineVersion = requestUrl.searchParams.get('lineVersion') ?? '';
  const sportsbook = requestUrl.searchParams.get('sportsbook') ?? 'dk';

  if (!SUPPORTED_SPORTSBOOKS.has(sportsbook)) {
    return errorResponse(400, 'A valid sportsbook is required.');
  }

  try {
    const payload = await fetchHotPayload(
      selectedDate,
      lineVersion,
      sportsbook as 'dk' | 'fd' | 'mgm' | 'cz' | 'pp',
    );
    return jsonResponse(payload, {
      cache: {
        browserMaxAge: 0,
        sMaxAge: 10,
        staleWhileRevalidate: 30,
      },
    });
  } catch (error) {
    console.error('[api/hot]', error);
    return errorResponse(500, 'Failed to load live dashboard updates.');
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
