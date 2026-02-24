export default {
    async fetch(request) {
        const url = new URL(request.url);
        const targetUrl = url.searchParams.get("url");

        if (!targetUrl) {
            return new Response("Missing url parameter. Usage: ?url=https://...", { status: 400 });
        }

        let targetObj;
        try {
            targetObj = new URL(targetUrl);
        } catch (e) {
            return new Response("Invalid target URL provided.", { status: 400 });
        }

        const userAgents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
        ];

        const randomUA = userAgents[Math.floor(Math.random() * userAgents.length)];

        const modifiedRequest = new Request(targetUrl, {
            method: request.method,
            headers: request.headers,
        });

        modifiedRequest.headers.delete("cf-connecting-ip");
        modifiedRequest.headers.delete("cf-ipcountry");
        modifiedRequest.headers.delete("cf-ray");
        modifiedRequest.headers.delete("cf-visitor");
        modifiedRequest.headers.delete("x-forwarded-proto");
        modifiedRequest.headers.delete("x-forwarded-for");
        modifiedRequest.headers.delete("x-real-ip");

        // Spoof perfect browser headers
        modifiedRequest.headers.set("User-Agent", randomUA);
        modifiedRequest.headers.set("Accept", "application/json, text/plain, */*");
        modifiedRequest.headers.set("Accept-Language", "en-US,en;q=0.9");
        modifiedRequest.headers.set("Origin", targetObj.origin);
        modifiedRequest.headers.set("Referer", `${targetObj.origin}/`);

        // Add sec-ch-ua headers (mostly expected from Chrome/Edge)
        if (randomUA.includes("Chrome")) {
            modifiedRequest.headers.set("sec-ch-ua", '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"');
            modifiedRequest.headers.set("sec-ch-ua-mobile", "?0");
            modifiedRequest.headers.set("sec-ch-ua-platform", randomUA.includes("Windows") ? '"Windows"' : '"macOS"');
        }

        modifiedRequest.headers.set("Sec-Fetch-Dest", "empty");
        modifiedRequest.headers.set("Sec-Fetch-Mode", "cors");
        modifiedRequest.headers.set("Sec-Fetch-Site", "cross-site");
        modifiedRequest.headers.set("Connection", "keep-alive");

        try {
            const response = await fetch(modifiedRequest);

            const newResponse = new Response(response.body, response);
            newResponse.headers.set("Access-Control-Allow-Origin", "*");
            return newResponse;

        } catch (e) {
            return new Response("Error fetching target URL: " + e.message, { status: 500 });
        }
    },
};
