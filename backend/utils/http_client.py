"""
http_client.py
One hardened HTTP path for every upstream that blocks datacenter IPs.

Why this exists
---------------
Measured from the Oracle VM (2026-10-20), with no proxy:

    cdn.nba.com        -> 403 Forbidden
    stats.nba.com      -> connection hangs, 25s timeout, no response at all
    official.nba.com   -> 403 Forbidden in 0.13s

Every one of those succeeds through the Cloudflare Worker. So on this host a
"fall back to a direct connection" path is not a fallback, it is a guaranteed
failure that also burns 25s of timeout and advertises the IP to the upstream.
`proxied_get` therefore refuses to run without a worker URL rather than
silently degrading.

Design notes, each tied to an observed worker behaviour
------------------------------------------------------
  * The worker samples a random User-Agent from a 4-entry pool per request, and
    its own header comment warns that a UA which ages out of support produces
    INTERMITTENT 403s. The worker does not retry 403 (403 is absent from its
    RETRYABLE_STATUS_CODES). So a 403 is retried HERE: each retry re-enters the
    worker and draws a fresh UA, which converts a ~1-in-4 stale-UA failure into
    a near-certain success. This is the single most valuable behaviour in this
    module.

  * The worker's own rate limiter is 60 requests / 60s per client identifier,
    and tripping it returns 429 -- which would compound the upstream's own
    limiting. We pace client-side to stay well clear of that ceiling.

  * The worker allows up to 2 upstream attempts at 15s each, so a single call
    can legitimately take ~30s before returning. Client timeouts must exceed
    that or we abort work the worker was about to complete successfully.

  * The worker returns its OWN errors as JSON with an "error" key (e.g. 403
    "Target host is not allowed.", 400 "Missing url parameter."). Those are
    configuration faults, not upstream blocks -- retrying them is pointless and
    just burns the rate-limit budget. They are detected and raised immediately.

Latency is explicitly not a goal here. Correctness and not getting blocked are.
"""

import json
import logging
import os
import random
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from dotenv import load_dotenv

# Load backend/.env here rather than relying on the caller. In production
# master_cron loads it before importing anything, so PBPSTATS_PROXY_URL is
# visible; any standalone entry point (run_pipeline.py, utils/roster_sync.py,
# a scraper run by hand) otherwise sees no proxy URL and proxied_get refuses,
# which looks exactly like a missing config. load_dotenv does not override
# variables already in the environment, so the production path is unchanged.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logger = logging.getLogger("http_client")

# Hosts the worker is configured to accept. Anything else must be added to the
# worker's ALLOWED_UPSTREAM_HOSTS before it can be proxied.
WORKER_ALLOWED_HOSTS = frozenset({
    "ak-static.cms.nba.com",
    "api.pbpstats.com",
    "api.prizepicks.com",
    "api.sportsbook.fanduel.com",
    "cdn.nba.com",
    "official.nba.com",
    "sportsbook-nash.draftkings.com",
    "sportsbook.draftkings.com",
    "stats.nba.com",
    "www.nba.com",
})

# Retried here. 403 is deliberate -- see the module docstring. 408/425 and the
# Cloudflare 52x family are transient edge conditions, not real refusals.
RETRY_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504,
                            520, 521, 522, 523, 524, 525, 526, 530})

# The worker needs ~30s worst case (2 upstream attempts x 15s). Anything less
# and we cancel work that was about to succeed.
DEFAULT_TIMEOUT = float(os.environ.get("PROXY_REQUEST_TIMEOUT", "60"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("PROXY_MAX_ATTEMPTS", "6"))

# Worker limit is 60/60s. 1.1s spacing => ~54/min, leaving headroom for the
# retries this module issues on top of the baseline request rate.
MIN_REQUEST_INTERVAL = float(os.environ.get("PROXY_MIN_INTERVAL", "1.1"))

BACKOFF_BASE = float(os.environ.get("PROXY_BACKOFF_BASE", "2.0"))
BACKOFF_CAP = float(os.environ.get("PROXY_BACKOFF_CAP", "45"))

_pace_lock = threading.Lock()
_last_request_at = 0.0

# Adaptive pacing. Surviving a 429 is not the goal -- not provoking one is. Every
# rate-limit or block signal widens the spacing between ALL subsequent requests;
# sustained clean responses walk it back down. Additive-increase /
# multiplicative-decrease, so we back off fast and recover slowly.
_pace_interval = MIN_REQUEST_INTERVAL
_clean_streak = 0
PACE_CEILING = float(os.environ.get("PROXY_PACE_CEILING", "12"))
PACE_PENALTY = float(os.environ.get("PROXY_PACE_PENALTY", "1.5"))
PACE_RECOVER_AFTER = int(os.environ.get("PROXY_PACE_RECOVER_AFTER", "20"))


def _note_throttled(status):
    """A 429/403 widens pacing for everything that follows."""
    global _pace_interval, _clean_streak
    with _pace_lock:
        _clean_streak = 0
        before = _pace_interval
        _pace_interval = min(_pace_interval + PACE_PENALTY, PACE_CEILING)
        if _pace_interval != before:
            logger.warning("rate-limit signal (HTTP %s): pacing widened %.1fs -> %.1fs",
                           status, before, _pace_interval)


def _note_clean():
    """Enough consecutive clean responses and we relax one step."""
    global _pace_interval, _clean_streak
    with _pace_lock:
        _clean_streak += 1
        if _clean_streak >= PACE_RECOVER_AFTER and _pace_interval > MIN_REQUEST_INTERVAL:
            before = _pace_interval
            _pace_interval = max(MIN_REQUEST_INTERVAL, _pace_interval - PACE_PENALTY / 2)
            _clean_streak = 0
            logger.info("pacing relaxed %.1fs -> %.1fs after %d clean responses",
                        before, _pace_interval, PACE_RECOVER_AFTER)


def current_pace():
    """Current inter-request spacing, for logging/diagnostics."""
    return _pace_interval
_session = None
_session_lock = threading.Lock()


class ProxyNotConfigured(RuntimeError):
    """No worker URL. On a datacenter IP this is fatal, not a warning."""


class WorkerRejected(RuntimeError):
    """The worker refused the request itself (bad host, missing param, auth)."""


class UpstreamFailed(RuntimeError):
    """Upstream kept failing after every attempt was exhausted."""


# Hop-by-hop / transport headers. These describe the caller->worker leg, not the
# worker->upstream leg, and forwarding them breaks things. The live failure that
# motivated this: a scraper sent "accept-encoding: gzip, deflate, br, zstd",
# Cloudflare honoured it and returned zstd, and requests cannot decode zstd --
# so a perfectly good 200 arrived as binary garbage. The worker sets its own
# Accept-Encoding for the upstream, so callers must not dictate transport here.
_HOP_BY_HOP = frozenset({
    "accept-encoding", "connection", "keep-alive", "host", "content-length",
    "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
})


def _sanitize_headers(headers):
    """Strip transport headers, keeping the semantic ones the upstream cares about."""
    if not headers:
        return None
    cleaned = {k: v for k, v in headers.items()
               if k.lower() not in _HOP_BY_HOP}
    return cleaned or None


def _merge_params(url, params):
    """Fold `params` into `url`'s query string, preserving anything already there.

    Values are urlencoded once here. The worker passes the decoded `url` straight
    through to the upstream, so double-encoding would corrupt things like the
    `SeasonType=Regular+Season` values stats.nba.com expects.
    """
    parsed = urlparse(url)
    merged = parse_qsl(parsed.query, keep_blank_values=True)
    existing = {k for k, _ in merged}
    for key, value in (params.items() if hasattr(params, "items") else params):
        if value is None:
            continue
        if key in existing:
            merged = [(k, v) for k, v in merged if k != key]
        merged.append((key, str(value)))
    return urlunparse(parsed._replace(query=urlencode(merged)))


def get_worker_url():
    raw = (os.environ.get("PBPSTATS_PROXY_URL") or "").strip().strip('"').strip("'")
    if not raw:
        raise ProxyNotConfigured(
            "PBPSTATS_PROXY_URL is not set. Direct requests to stats.nba.com / "
            "cdn.nba.com / official.nba.com are blocked from this host (403 or "
            "hang), so there is no usable fallback. Set it in backend/.env."
        )
    return raw


def _get_session():
    """One pooled session: a single TLS handshake to Cloudflare, reused.

    Deliberately NO urllib3 Retry adapter. Retries are driven in-process so we
    can respect Retry-After, apply jitter, distinguish worker faults from
    upstream ones, and keep the pacing lock honest -- none of which urllib3's
    blind retry can do.
    """
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers.update({
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            })
            token = (os.environ.get("PBPSTATS_PROXY_TOKEN")
                     or os.environ.get("PROXY_SHARED_SECRET") or "").strip()
            if token:
                # Worker checks X-Proxy-Token only when PROXY_SHARED_SECRET is
                # set on its side; harmless when it is not.
                s.headers["X-Proxy-Token"] = token
            _session = s
        return _session


def _pace():
    """Serialize outbound requests so we never approach the worker's 60/60s."""
    global _last_request_at
    with _pace_lock:
        interval = _pace_interval
        delta = time.monotonic() - _last_request_at
        wait = interval - delta
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.25))
        _last_request_at = time.monotonic()


def _worker_error(response):
    """Return the worker's own error message, or None if this is upstream data.

    The worker emits JSON {"error": "..."} for its own faults. Upstream payloads
    are also JSON, so match on the shape: a short object whose only meaningful
    key is "error".
    """
    ctype = (response.headers.get("Content-Type") or "").lower()
    if "json" not in ctype or len(response.content) > 512:
        return None
    try:
        body = json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(body, dict) and "error" in body and len(body) <= 2:
        return str(body["error"])
    return None


def _sleep_for(attempt, response):
    """Retry-After when the server states it, else exponential full jitter."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), BACKOFF_CAP)
            except ValueError:
                pass
    ceiling = min(BACKOFF_BASE ** attempt, BACKOFF_CAP)
    return random.uniform(ceiling / 2, ceiling)


def proxied_get(url, *, headers=None, params=None, timeout=None,
                max_attempts=None, label=None):
    """GET `url` through the Cloudflare Worker, retrying intelligently.

    Raises ProxyNotConfigured, WorkerRejected or UpstreamFailed. Never returns a
    non-2xx response and never silently falls back to a direct connection.
    """
    worker = get_worker_url()
    timeout = timeout or DEFAULT_TIMEOUT
    max_attempts = max_attempts or DEFAULT_MAX_ATTEMPTS
    tag = label or urlparse(url).hostname or "upstream"

    host = (urlparse(url).hostname or "").lower()
    if host not in WORKER_ALLOWED_HOSTS:
        raise WorkerRejected(
            f"{host} is not in the worker's allowed upstream hosts. Add it to "
            f"ALLOWED_UPSTREAM_HOSTS on the worker before proxying it. "
            f"Known-good: {sorted(WORKER_ALLOWED_HOSTS)}"
        )

    session = _get_session()
    headers = _sanitize_headers(headers)
    # `params` belong to the UPSTREAM request, so they must be encoded into the
    # url the worker forwards. Putting them in the worker's own query string
    # would silently drop them -- the worker only forwards `url`.
    if params:
        url = _merge_params(url, params)
    query = {"url": url}

    last_status = None
    for attempt in range(1, max_attempts + 1):
        _pace()
        response = None
        try:
            response = session.get(worker, params=query, headers=headers,
                                   timeout=timeout)
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise UpstreamFailed(
                    f"{tag}: transport error after {max_attempts} attempts: {exc}"
                ) from exc
            delay = _sleep_for(attempt, None)
            logger.warning("%s: %s (attempt %d/%d), retrying in %.1fs",
                           tag, type(exc).__name__, attempt, max_attempts, delay)
            time.sleep(delay)
            continue

        # The worker's own faults are configuration problems: fail fast.
        worker_msg = _worker_error(response)
        if worker_msg is not None:
            raise WorkerRejected(f"{tag}: worker refused request: {worker_msg}")

        if response.ok:
            _note_clean()
            if attempt > 1:
                logger.info("%s: recovered on attempt %d/%d (HTTP %d)",
                            tag, attempt, max_attempts, response.status_code)
            return response

        last_status = response.status_code
        if response.status_code not in RETRY_STATUSES or attempt == max_attempts:
            raise UpstreamFailed(
                f"{tag}: HTTP {response.status_code} after {attempt} attempt(s)"
            )

        if response.status_code in (403, 429):
            _note_throttled(response.status_code)
        delay = _sleep_for(attempt, response)
        # A 403 here is usually the worker having drawn a stale UA from its
        # 4-entry pool; the next attempt re-rolls it.
        reason = ("403 - likely a stale UA from the worker pool, re-rolling"
                  if response.status_code == 403 else f"HTTP {response.status_code}")
        logger.warning("%s: %s (attempt %d/%d), retrying in %.1fs",
                       tag, reason, attempt, max_attempts, delay)
        time.sleep(delay)

    raise UpstreamFailed(f"{tag}: exhausted {max_attempts} attempts "
                         f"(last status {last_status})")


def proxied_get_json(url, **kwargs):
    """proxied_get + JSON decode, with the body excerpt on a decode failure."""
    response = proxied_get(url, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        excerpt = response.text[:200].replace("\n", " ")
        raise UpstreamFailed(
            f"{kwargs.get('label') or url}: response was not JSON: {excerpt!r}"
        ) from exc
