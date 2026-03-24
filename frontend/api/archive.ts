import { readArchiveToken } from './_lib/access.js';
import { fetchArchivePayload } from './_lib/dashboardData.js';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
  rejectBrowserNavigation,
  rejectCrossSiteBrowserRequest,
  rejectUnknownAppClient,
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

    const navigationResponse = rejectBrowserNavigation(request);
    if (navigationResponse) {
      return navigationResponse;
    }

    const clientResponse = rejectUnknownAppClient(request);
    if (clientResponse) {
      return clientResponse;
    }

    const rateLimitResponse = enforceRateLimit(request, {
      bucket: 'archive',
      limit: 45,
      windowMs: 60_000,
    });
    if (rateLimitResponse) {
      return rateLimitResponse;
    }

    const requestUrl = new URL(request.url);
    const token = requestUrl.searchParams.get('token') ?? '';
    const archiveAccess = readArchiveToken(request, token);

    if (!archiveAccess) {
      return errorResponse(403, 'A valid archive access token is required.');
    }

    try {
      const payload = await fetchArchivePayload(archiveAccess.playerId, archiveAccess.season);
      return jsonResponse(payload);
    } catch (error) {
      console.error('[api/archive]', error);
      return errorResponse(500, 'Failed to load archived game logs.');
    }
  },
};
