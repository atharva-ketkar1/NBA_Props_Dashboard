const USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
];

const DEFAULT_ALLOWED_UPSTREAM_HOSTS = new Set([
    "api.pbpstats.com",
    "api.sportsbook.fanduel.com",
    "cdn.nba.com",
    "sportsbook-nash.draftkings.com",
    "sportsbook.draftkings.com",
    "stats.nba.com",
    "www.nba.com",
]);

const RETRYABLE_STATUS_CODES = new Set([429, 500, 502, 503, 504, 520, 521, 522, 524, 525, 526, 530]);
const rateBuckets = new Map();

function parseCsv(rawValue) {
    return String(rawValue || "")
        .split(",")
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean);
}

function randomUserAgent() {
    return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function getAllowedUpstreamHosts(env) {
    const configuredHosts = parseCsv(env.ALLOWED_UPSTREAM_HOSTS);
    return configuredHosts.length > 0 ? new Set(configuredHosts) : DEFAULT_ALLOWED_UPSTREAM_HOSTS;
}

function getAllowedOrigins(env) {
    return new Set(parseCsv(env.ALLOWED_ORIGINS));
}

function getCorsOrigin(request, env) {
    const origin = request.headers.get("Origin");
    if (!origin) {
        return null;
    }

    const allowedOrigins = getAllowedOrigins(env);
    if (!allowedOrigins.size) {
        return null;
    }

    return allowedOrigins.has(origin.toLowerCase()) ? origin : null;
}

function applyResponseHeaders(baseHeaders, request, env) {
    const headers = new Headers(baseHeaders);
    const corsOrigin = getCorsOrigin(request, env);

    headers.set("Cache-Control", "no-store");
    headers.set("Vary", "Origin");
    headers.set("X-Content-Type-Options", "nosniff");

    if (corsOrigin) {
        headers.set("Access-Control-Allow-Origin", corsOrigin);
        headers.set("Access-Control-Allow-Methods", "GET,HEAD,OPTIONS");
        headers.set("Access-Control-Allow-Headers", "Content-Type, X-Proxy-Token");
    }

    return headers;
}

function jsonResponse(payload, status, request, env) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: applyResponseHeaders(
            {
                "Content-Type": "application/json; charset=UTF-8",
            },
            request,
            env,
        ),
    });
}

function buildUpstreamHeaders(request, targetObj, userAgent) {
    const headers = new Headers();
    const acceptLanguage = request.headers.get("Accept-Language") || "en-US,en;q=0.9";

    headers.set("Accept", "application/json, text/plain, */*");
    headers.set("Accept-Language", acceptLanguage);
    headers.set("Cache-Control", "no-cache");
    headers.set("Origin", `${targetObj.protocol}//${targetObj.host}`);
    headers.set("Pragma", "no-cache");
    headers.set("Referer", `${targetObj.protocol}//${targetObj.host}/`);
    headers.set("Sec-Fetch-Dest", "empty");
    headers.set("Sec-Fetch-Mode", "cors");
    headers.set("Sec-Fetch-Site", "same-site");
    headers.set("User-Agent", userAgent);

    if (userAgent.includes("Chrome") || userAgent.includes("Edge")) {
        headers.set("sec-ch-ua", '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"');
        headers.set("sec-ch-ua-mobile", "?0");
        headers.set("sec-ch-ua-platform", userAgent.includes("Windows") ? '"Windows"' : '"macOS"');
    }

    return headers;
}

function isPrivateHostname(hostname) {
    const normalized = hostname.toLowerCase();

    if (["0.0.0.0", "127.0.0.1", "[::1]", "localhost"].includes(normalized)) {
        return true;
    }

    if (normalized.endsWith(".internal") || normalized.endsWith(".local")) {
        return true;
    }

    const ipv4Match = normalized.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
    if (!ipv4Match) {
        return false;
    }

    const octets = ipv4Match.slice(1).map((value) => Number(value));
    const [first, second] = octets;

    return (
        first === 0 ||
        first === 10 ||
        first === 127 ||
        (first === 169 && second === 254) ||
        (first === 172 && second >= 16 && second <= 31) ||
        (first === 192 && second === 168)
    );
}

function isTargetAllowed(targetObj, env) {
    if (!["http:", "https:"].includes(targetObj.protocol)) {
        return false;
    }

    if (isPrivateHostname(targetObj.hostname)) {
        return false;
    }

    return getAllowedUpstreamHosts(env).has(targetObj.hostname.toLowerCase());
}

function getClientIdentifier(request) {
    return (
        request.headers.get("CF-Connecting-IP") ||
        request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
        "unknown"
    );
}

function checkRateLimit(request, env) {
    const rawLimit = Number(env.RATE_LIMIT_MAX || 60);
    const rawWindowMs = Number(env.RATE_LIMIT_WINDOW_MS || 60_000);
    const limit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : 60;
    const windowMs = Number.isFinite(rawWindowMs) && rawWindowMs > 0 ? rawWindowMs : 60_000;
    const key = getClientIdentifier(request);
    const now = Date.now();
    const recentRequests = (rateBuckets.get(key) || []).filter((timestamp) => now - timestamp < windowMs);

    if (recentRequests.length >= limit) {
        rateBuckets.set(key, recentRequests);
        return false;
    }

    recentRequests.push(now);
    rateBuckets.set(key, recentRequests);
    return true;
}

function hasValidSharedSecret(request, env) {
    const expectedSecret = String(env.PROXY_SHARED_SECRET || "").trim();
    if (!expectedSecret) {
        return true;
    }

    return request.headers.get("X-Proxy-Token") === expectedSecret;
}

async function fetchWithRetry(request, targetUrl, targetObj) {
    let lastResponse = null;
    let lastError = null;

    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            const response = await fetch(targetUrl, {
                method: "GET",
                headers: buildUpstreamHeaders(request, targetObj, randomUserAgent()),
                redirect: "manual",
                cf: {
                    cacheEverything: false,
                    cacheTtl: 0,
                },
            });

            if (response.status >= 300 && response.status < 400) {
                return new Response(JSON.stringify({ error: "Upstream redirects are blocked." }), {
                    status: 502,
                    headers: {
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                });
            }

            if (!RETRYABLE_STATUS_CODES.has(response.status)) {
                return response;
            }

            lastResponse = response;
        } catch (error) {
            lastError = error;
        }

        if (attempt < 2) {
            await sleep(750 * (attempt + 1));
        }
    }

    if (lastResponse) {
        return lastResponse;
    }

    throw lastError || new Error("Proxy fetch failed without a response.");
}

export default {
    async fetch(request, env) {
        if (request.method === "OPTIONS") {
            return new Response(null, {
                status: 204,
                headers: applyResponseHeaders({}, request, env),
            });
        }

        if (!["GET", "HEAD"].includes(request.method)) {
            return jsonResponse({ error: "Method not allowed." }, 405, request, env);
        }

        if (!hasValidSharedSecret(request, env)) {
            return jsonResponse({ error: "Unauthorized." }, 401, request, env);
        }

        if (!checkRateLimit(request, env)) {
            return jsonResponse({ error: "Rate limit exceeded." }, 429, request, env);
        }

        const requestUrl = new URL(request.url);
        const targetUrl = requestUrl.searchParams.get("url");

        if (!targetUrl) {
            return jsonResponse({ error: "Missing url parameter." }, 400, request, env);
        }

        let targetObj;
        try {
            targetObj = new URL(targetUrl);
        } catch {
            return jsonResponse({ error: "Invalid target URL provided." }, 400, request, env);
        }

        if (!isTargetAllowed(targetObj, env)) {
            return jsonResponse({ error: "Target host is not allowed." }, 403, request, env);
        }

        try {
            const response = await fetchWithRetry(request, targetUrl, targetObj);
            const headers = applyResponseHeaders({}, request, env);
            const contentType = response.headers.get("Content-Type");

            if (contentType) {
                headers.set("Content-Type", contentType);
            }

            headers.set("X-Proxy-Target", targetObj.hostname);

            return new Response(request.method === "HEAD" ? null : response.body, {
                status: response.status,
                statusText: response.statusText,
                headers,
            });
        } catch {
            return jsonResponse({ error: "Proxy fetch failed." }, 500, request, env);
        }
    },
};
