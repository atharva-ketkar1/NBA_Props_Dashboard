import { fetchBootstrapPayload } from './_lib/dashboardData.js';
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
      bucket: 'bootstrap',
      limit: 20,
      windowMs: 60_000,
    });
    if (rateLimitResponse) {
      return rateLimitResponse;
    }

    try {
      const payload = await fetchBootstrapPayload();
      return jsonResponse(payload, {
        cache: {
          browserMaxAge: 0,
          sMaxAge: 60,
          staleWhileRevalidate: 300,
        },
      });
    } catch (error) {
      console.error('[api/bootstrap]', error);
      return errorResponse(500, 'Failed to load dashboard bootstrap data.');
    }
  },
};
