const USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
];

const RETRYABLE_STATUS_CODES = new Set([429, 500, 502, 503, 504, 520, 521, 522, 524, 525, 526, 530]);

function randomUserAgent() {
    return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildUpstreamHeaders(request, targetObj, userAgent) {
    const headers = new Headers();
    const origin = request.headers.get("Origin") || `${targetObj.protocol}//${targetObj.host}`;
    const referer = request.headers.get("Referer") || `${targetObj.protocol}//${targetObj.host}/`;
    const acceptLanguage = request.headers.get("Accept-Language") || "en-US,en;q=0.9";

    headers.set("Accept", "application/json, text/plain, */*");
    headers.set("Accept-Language", acceptLanguage);
    headers.set("Cache-Control", "no-cache");
    headers.set("Pragma", "no-cache");
    headers.set("Origin", origin);
    headers.set("Referer", referer);
    headers.set("User-Agent", userAgent);
    headers.set("Sec-Fetch-Dest", "empty");
    headers.set("Sec-Fetch-Mode", "cors");
    headers.set("Sec-Fetch-Site", "same-site");

    if (userAgent.includes("Chrome") || userAgent.includes("Edge")) {
        headers.set("sec-ch-ua", '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"');
        headers.set("sec-ch-ua-mobile", "?0");
        headers.set("sec-ch-ua-platform", userAgent.includes("Windows") ? '"Windows"' : '"macOS"');
    }

    return headers;
}

async function fetchWithRetry(request, targetUrl, targetObj) {
    let lastResponse = null;
    let lastError = null;

    for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
            const response = await fetch(targetUrl, {
                method: "GET",
                headers: buildUpstreamHeaders(request, targetObj, randomUserAgent()),
                redirect: "follow",
                cf: {
                    cacheEverything: false,
                    cacheTtl: 0,
                },
            });

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
    async fetch(request) {
        const requestUrl = new URL(request.url);
        const targetUrl = requestUrl.searchParams.get("url");

        if (!targetUrl) {
            return new Response("Missing url parameter. Usage: ?url=https://...", { status: 400 });
        }

        let targetObj;
        try {
            targetObj = new URL(targetUrl);
        } catch (error) {
            return new Response("Invalid target URL provided.", { status: 400 });
        }

        try {
            const response = await fetchWithRetry(request, targetUrl, targetObj);
            const headers = new Headers(response.headers);
            headers.set("Access-Control-Allow-Origin", "*");
            headers.set("Cache-Control", "no-store");
            headers.set("X-Proxy-Target", targetObj.hostname);

            return new Response(response.body, {
                status: response.status,
                statusText: response.statusText,
                headers,
            });
        } catch (error) {
            return new Response(
                JSON.stringify({
                    error: "Proxy fetch failed",
                    details: error.message,
                    target: targetUrl,
                }),
                {
                    status: 500,
                    headers: {
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "no-store",
                        "Content-Type": "application/json",
                    },
                }
            );
        }
    },
};
