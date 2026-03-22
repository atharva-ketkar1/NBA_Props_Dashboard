import { fetchBootstrapPayload } from './_lib/dashboardData';
import {
  enforceRateLimit,
  errorResponse,
  jsonResponse,
  methodNotAllowed,
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
      bucket: 'bootstrap',
      limit: 30,
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
