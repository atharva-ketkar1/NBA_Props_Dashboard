import { fetchEdgePayload } from './_lib/dashboardData.js';
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
    bucket: 'edge',
    limit: 60,
    windowMs: 60_000,
  });
  if (rateLimitResponse) {
    return rateLimitResponse;
  }

  try {
    const payload = await fetchEdgePayload();
    return jsonResponse(payload, {
      cache: {
        browserMaxAge: 0,
        sMaxAge: 15,
        staleWhileRevalidate: 60,
      },
    });
  } catch (error) {
    console.error('[api/edge]', error);
    return errorResponse(500, 'Failed to load Signal Score rankings.');
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
