import pandas as pd
import requests
import time
import random
import urllib.parse
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# ==========================================
# CONFIGURATION
# ==========================================
UPDATE_WINDOW_DAYS = 2
MAX_HISTORY_GAMES = 85
MAX_WORKERS = 1
REQUEST_TIMEOUT = 60
BASE_LOG_MAX_RETRIES = 5
ENDPOINT_MAX_RETRIES = 3
FRAGILE_ENDPOINT_MAX_RETRIES = int(os.environ.get("GAMELOGS_FRAGILE_ENDPOINT_MAX_RETRIES", "5"))
INCREMENTAL_BASE_SLEEP_SECONDS = float(os.environ.get("GAMELOGS_INCREMENTAL_BASE_SLEEP_SECONDS", "2.5"))
FULL_REFRESH_BASE_SLEEP_SECONDS = float(os.environ.get("GAMELOGS_FULL_REFRESH_BASE_SLEEP_SECONDS", "4.5"))
SEGMENT_ENDPOINT_EXTRA_SLEEP_SECONDS = float(os.environ.get("GAMELOGS_SEGMENT_ENDPOINT_EXTRA_SLEEP_SECONDS", "4.0"))
ADVANCED_ENDPOINT_EXTRA_SLEEP_SECONDS = float(os.environ.get("GAMELOGS_ADVANCED_ENDPOINT_EXTRA_SLEEP_SECONDS", "2.5"))
EDGE_ERROR_EXTRA_COOLDOWN_SECONDS = float(os.environ.get("GAMELOGS_EDGE_ERROR_EXTRA_COOLDOWN_SECONDS", "12.0"))
RETRYABLE_STATUS_CODES = {
    408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524, 525, 526, 530
}
EDGE_RETRYABLE_STATUS_CODES = {520, 521, 522, 524, 525, 526, 530}

# Default season to scrape. The output CSV will be named gamelogs_{SEASON}.csv automatically.
SEASON = "2025-26"

# Seasons that are fully complete. When explicitly requested, the script will
# do a one-time full fetch if the CSV doesn't exist yet, then never update again.
COMPLETED_SEASONS = {"2024-25"}

HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "dnt": "1",
    "origin": "https://www.nba.com",
    "referer": "https://www.nba.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
}

PT_MEASURE_TYPES = ["Passing", "Rebounding", "Drives"]

# Core box score columns that should be filled with 0 if missing.
# Tracking/advanced columns are intentionally left as NaN to signal missing data vs. true zero.
CORE_FILL_ZERO_COLS = [
    'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'PF',
    'FGM', 'FGA', 'FG3M', 'FG3A', 'FTM', 'FTA', 'PLUS_MINUS'
]

# Global cooldown tracker — shared across all fetch calls during full refresh
_last_500_time = 0


class NBAStatsRequestError(RuntimeError):
    def __init__(self, message, status_code=None, route_label=None):
        super().__init__(message)
        self.status_code = status_code
        self.route_label = route_label


def _safe_pct_convert(df, cols):
    """
    Converts decimal percentage columns (e.g. 0.284) to whole-number form (28.4).
    Guard: only multiplies if the column's max value is <= 1.0.
    """
    for col in cols:
        if col in df.columns:
            if df[col].dropna().empty:
                continue
            if df[col].max() <= 1.0:
                df[col] = (df[col] * 100).round(1)
    return df


def _split_proxy_urls(raw_value):
    if not raw_value:
        return []
    urls = []
    for chunk in raw_value.replace("\n", ",").split(","):
        clean = chunk.strip()
        if clean:
            urls.append(clean)
    return urls


def _build_request_targets():
    targets = []
    seen = set()

    for proxy_url in _split_proxy_urls(os.environ.get("PBPSTATS_PROXY_URL")) + _split_proxy_urls(os.environ.get("PBPSTATS_PROXY_URLS")):
        if proxy_url not in seen:
            seen.add(proxy_url)
            targets.append({
                "url": proxy_url,
                "label": urllib.parse.urlparse(proxy_url).netloc or proxy_url,
            })

    allow_direct_fallback = os.environ.get("GAMELOGS_ALLOW_DIRECT_FALLBACK", "").strip().lower() in {"1", "true", "yes"}
    if not targets or allow_direct_fallback:
        targets.append({"url": None, "label": "direct"})

    return targets


def _summarize_response_body(response, limit=180):
    try:
        body = response.text
    except Exception:
        return ""

    if not body:
        return ""

    compact = " ".join(body.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _retry_wait_seconds(exc, attempt_number, is_full_refresh=False):
    status_code = getattr(exc, "status_code", None)
    message = str(exc).lower()

    if status_code in EDGE_RETRYABLE_STATUS_CODES:
        base_wait = 20 if is_full_refresh else 12
    elif status_code == 429:
        base_wait = 16 if is_full_refresh else 8
    elif status_code in RETRYABLE_STATUS_CODES:
        base_wait = 12 if is_full_refresh else 6
    elif isinstance(exc, requests.Timeout) or "timed out" in message:
        base_wait = 10 if is_full_refresh else 5
    else:
        base_wait = 8 if is_full_refresh else 4

    max_wait = 120 if is_full_refresh else 60
    return min(base_wait * attempt_number, max_wait)


def _request_sleep_seconds(base_sleep, extra_sleep=0.0):
    jitter = random.uniform(0.25, 0.9) if extra_sleep > 0 else random.uniform(0.0, 0.35)
    return base_sleep + extra_sleep + jitter


def _retry_wait_with_edge_cooldown(exc, attempt_number, is_full_refresh=False):
    retry_wait = _retry_wait_seconds(exc, attempt_number, is_full_refresh=is_full_refresh)
    if getattr(exc, "status_code", None) in EDGE_RETRYABLE_STATUS_CODES:
        edge_wait = EDGE_ERROR_EXTRA_COOLDOWN_SECONDS + ((attempt_number - 1) * 4)
        retry_wait = max(retry_wait, edge_wait)
    return retry_wait


def _result(status, message, **extra):
    payload = {"status": status, "message": message}
    payload.update(extra)
    return payload


def make_proxied_request(url, headers=None, timeout=REQUEST_TIMEOUT, attempt_index=0):
    request_targets = _build_request_targets()
    if not request_targets:
        raise NBAStatsRequestError("No request targets are configured for NBA stats fetches.")

    errors = []
    last_error = None

    for offset in range(len(request_targets)):
        target = request_targets[(attempt_index + offset) % len(request_targets)]
        route_label = target["label"]
        try:
            if target["url"]:
                response = requests.get(target["url"], params={"url": url}, headers=headers, timeout=timeout)
            else:
                response = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            errors.append(f"{route_label}: {type(exc).__name__} ({exc})")
            continue

        if response.status_code == 200:
            return response, route_label

        body_summary = _summarize_response_body(response)
        detail = f"{route_label} returned HTTP {response.status_code}"
        if body_summary:
            detail += f" | {body_summary}"
        last_error = NBAStatsRequestError(detail, status_code=response.status_code, route_label=route_label)
        errors.append(str(last_error))

    if errors:
        raise NBAStatsRequestError("All NBA stats request routes failed: " + "; ".join(errors))
    if isinstance(last_error, NBAStatsRequestError):
        raise last_error
    if last_error is not None:
        raise last_error
    raise NBAStatsRequestError("All NBA stats request routes failed without an error payload.")


def fetch_nba_json(url, headers=None, timeout=REQUEST_TIMEOUT, attempt_index=0, context="NBA stats request"):
    response, route_label = make_proxied_request(url, headers=headers, timeout=timeout, attempt_index=attempt_index)

    try:
        payload = response.json()
    except ValueError as exc:
        body_summary = _summarize_response_body(response)
        detail = f"{context} returned non-JSON payload via {route_label}"
        if body_summary:
            detail += f" | {body_summary}"
        raise NBAStatsRequestError(detail, route_label=route_label) from exc

    if not isinstance(payload, dict) or "resultSets" not in payload or not payload["resultSets"]:
        raise NBAStatsRequestError(f"{context} returned unexpected JSON payload via {route_label}", route_label=route_label)

    return payload, route_label


def fetch_tracking_data_for_date(date_str, is_full_refresh=False, season=None):
    """
    Fetches Drives, Passing, Rebounding, 1Q Stats, 1H Stats, and Advanced Stats
    for a single game date.
    
    Returns: (daily_merged, failed_endpoints)
    """
    global _last_500_time
    max_retries = ENDPOINT_MAX_RETRIES
    fragile_max_retries = max(max_retries, FRAGILE_ENDPOINT_MAX_RETRIES)
    if season is None:
        season = SEASON

    if is_full_refresh:
        elapsed = time.time() - _last_500_time
        if elapsed < 60:
            wait = 60 - elapsed
            print(f"      Global cooldown active. Waiting {wait:.1f}s before starting {date_str}...")
            time.sleep(wait)

    daily_merged = None
    failed_endpoints = []
    encoded_date = urllib.parse.quote(date_str, safe='')

    base_sleep = (
        FULL_REFRESH_BASE_SLEEP_SECONDS + random.uniform(0.5, 2.0)
        if is_full_refresh
        else INCREMENTAL_BASE_SLEEP_SECONDS + random.uniform(0.25, 0.75)
    )

    # -----------------------------------------------------------------------
    # 1. FETCH PT TRACKING DATA (Passing, Rebounds, Drives)
    # -----------------------------------------------------------------------
    for PT in PT_MEASURE_TYPES:
        for attempt in range(max_retries):
            try:
                time.sleep(_request_sleep_seconds(base_sleep))
                url = (
                    f"https://stats.nba.com/stats/leaguedashptstats"
                    f"?DateFrom={encoded_date}&DateTo={encoded_date}"
                    f"&LastNGames=0&LeagueID=00&Month=0&OpponentTeamID=0&PORound=0"
                    f"&PerMode=PerGame&PlayerOrTeam=Player&PtMeasureType={PT}"
                    f"&Season={season}&SeasonType=Regular%20Season&TeamID=0"
                )
                r, route_label = fetch_nba_json(
                    url,
                    headers=HEADERS,
                    timeout=REQUEST_TIMEOUT,
                    attempt_index=attempt,
                    context=f"{PT} on {date_str}",
                )

                if attempt > 0:
                    print(f"      Recovered {PT} on attempt {attempt + 1} via {route_label}.")

                row_set = r['resultSets'][0].get('rowSet', [])
                api_headers = r['resultSets'][0].get('headers', [])

                if not row_set:
                    break

                df_pt = pd.DataFrame(row_set, columns=api_headers)
                cols_to_keep = [
                    'PLAYER_ID', 'POTENTIAL_AST', 'PASSES_MADE', 'AST_POINTS_CREATED',
                    'REB_CHANCES', 'REB_CONTEST_PCT', 'DRIVES', 'DRIVE_PTS', 'DRIVE_PASSES'
                ]
                df_pt = df_pt[[c for c in cols_to_keep if c in df_pt.columns]]

                if daily_merged is None:
                    daily_merged = df_pt
                else:
                    daily_merged = pd.merge(daily_merged, df_pt, on='PLAYER_ID', how='outer')
                break

            except Exception as e:
                if getattr(e, "status_code", None) == 500 and is_full_refresh:
                    _last_500_time = time.time()
                retry_wait = _retry_wait_with_edge_cooldown(e, attempt + 1, is_full_refresh=is_full_refresh)
                print(f"      Attempt {attempt + 1} failed for {PT} on {date_str}: {type(e).__name__} ({e})")
                if attempt < max_retries - 1:
                    time.sleep(retry_wait)
                else:
                    print(f"      Fatal failure for {PT} on {date_str}. Saving partial.")
                    failed_endpoints.append(PT)
                    break

    # -----------------------------------------------------------------------
    # 2. FETCH 1ST QUARTER STATS
    # -----------------------------------------------------------------------
    for attempt in range(fragile_max_retries):
        try:
            time.sleep(_request_sleep_seconds(base_sleep, SEGMENT_ENDPOINT_EXTRA_SLEEP_SECONDS))
            q1_url = (
                f"https://stats.nba.com/stats/leaguedashplayerstats"
                f"?DateFrom={encoded_date}&DateTo={encoded_date}"
                f"&GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=Base"
                f"&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N"
                f"&PerMode=Totals&Period=1&PlusMinus=N&Rank=N"
                f"&Season={season}&SeasonSegment=&SeasonType=Regular%20Season"
                f"&ShotClockRange=&VsConference=&VsDivision="
            )
            r, route_label = fetch_nba_json(
                q1_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                attempt_index=attempt,
                context=f"1Q Stats on {date_str}",
            )

            if attempt > 0:
                print(f"      Recovered 1Q Stats on attempt {attempt + 1} via {route_label}.")

            row_set = r['resultSets'][0].get('rowSet', [])
            api_headers = r['resultSets'][0].get('headers', [])

            if row_set:
                df_1q = pd.DataFrame(row_set, columns=api_headers)
                q1_cols = ['PLAYER_ID', 'MIN', 'PTS', 'AST', 'REB', 'FG3M', 'FGM', 'FTM', 'PF']
                df_1q = df_1q[[c for c in q1_cols if c in df_1q.columns]]
                df_1q = df_1q.rename(columns={c: f"1Q_{c}" for c in q1_cols if c != 'PLAYER_ID'})

                if daily_merged is None:
                    daily_merged = df_1q
                else:
                    daily_merged = pd.merge(daily_merged, df_1q, on='PLAYER_ID', how='outer')
            break

        except Exception as e:
            if getattr(e, "status_code", None) == 500 and is_full_refresh:
                _last_500_time = time.time()
            retry_wait = _retry_wait_with_edge_cooldown(e, attempt + 1, is_full_refresh=is_full_refresh)
            print(f"      Attempt {attempt + 1} failed for 1Q Stats on {date_str}: {type(e).__name__} ({e})")
            if attempt < fragile_max_retries - 1:
                time.sleep(retry_wait)
            else:
                print(f"      Fatal failure for 1Q Stats on {date_str}. Saving partial.")
                failed_endpoints.append("1Q")
                break

    # -----------------------------------------------------------------------
    # 3. FETCH 1ST HALF STATS
    # -----------------------------------------------------------------------
    for attempt in range(fragile_max_retries):
        try:
            time.sleep(_request_sleep_seconds(base_sleep, SEGMENT_ENDPOINT_EXTRA_SLEEP_SECONDS))
            h1_url = (
                f"https://stats.nba.com/stats/leaguedashplayerstats"
                f"?DateFrom={encoded_date}&DateTo={encoded_date}"
                f"&GameSegment=First%20Half&LastNGames=0&LeagueID=00&Location=&MeasureType=Base"
                f"&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N"
                f"&PerMode=Totals&Period=0&PlusMinus=N&Rank=N"
                f"&Season={season}&SeasonSegment=&SeasonType=Regular%20Season"
                f"&ShotClockRange=&VsConference=&VsDivision="
            )
            r, route_label = fetch_nba_json(
                h1_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                attempt_index=attempt,
                context=f"1H Stats on {date_str}",
            )

            if attempt > 0:
                print(f"      Recovered 1H Stats on attempt {attempt + 1} via {route_label}.")

            row_set = r['resultSets'][0].get('rowSet', [])
            api_headers = r['resultSets'][0].get('headers', [])

            if row_set:
                df_1h = pd.DataFrame(row_set, columns=api_headers)
                h1_cols = ['PLAYER_ID', 'MIN', 'PTS', 'AST', 'REB', 'FG3M', 'FGM', 'FTM']
                df_1h = df_1h[[c for c in h1_cols if c in df_1h.columns]]
                df_1h = df_1h.rename(columns={c: f"1H_{c}" for c in h1_cols if c != 'PLAYER_ID'})

                if daily_merged is None:
                    daily_merged = df_1h
                else:
                    daily_merged = pd.merge(daily_merged, df_1h, on='PLAYER_ID', how='outer')
            break

        except Exception as e:
            if getattr(e, "status_code", None) == 500 and is_full_refresh:
                _last_500_time = time.time()
            retry_wait = _retry_wait_with_edge_cooldown(e, attempt + 1, is_full_refresh=is_full_refresh)
            print(f"      Attempt {attempt + 1} failed for 1H Stats on {date_str}: {type(e).__name__} ({e})")
            if attempt < fragile_max_retries - 1:
                time.sleep(retry_wait)
            else:
                print(f"      Fatal failure for 1H Stats on {date_str}. Saving partial.")
                failed_endpoints.append("1H")
                break

    # -----------------------------------------------------------------------
    # 4. FETCH ADVANCED STATS
    # -----------------------------------------------------------------------
    for attempt in range(fragile_max_retries):
        try:
            time.sleep(_request_sleep_seconds(base_sleep, ADVANCED_ENDPOINT_EXTRA_SLEEP_SECONDS))
            adv_url = (
                f"https://stats.nba.com/stats/leaguedashplayerstats"
                f"?DateFrom={encoded_date}&DateTo={encoded_date}"
                f"&GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=Advanced"
                f"&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N"
                f"&PerMode=Totals&Period=0&PlusMinus=N&Rank=N"
                f"&Season={season}&SeasonSegment=&SeasonType=Regular%20Season"
                f"&ShotClockRange=&VsConference=&VsDivision="
            )
            r, route_label = fetch_nba_json(
                adv_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                attempt_index=attempt,
                context=f"Advanced Stats on {date_str}",
            )

            if attempt > 0:
                print(f"      Recovered Advanced Stats on attempt {attempt + 1} via {route_label}.")

            row_set = r['resultSets'][0].get('rowSet', [])
            api_headers = r['resultSets'][0].get('headers', [])

            if row_set:
                df_adv = pd.DataFrame(row_set, columns=api_headers)
                adv_cols = ['PLAYER_ID', 'USG_PCT', 'AST_PCT', 'REB_PCT', 'TS_PCT', 'PIE']
                df_adv = df_adv[[c for c in adv_cols if c in df_adv.columns]]

                pct_cols = ['USG_PCT', 'AST_PCT', 'REB_PCT', 'TS_PCT', 'PIE']
                df_adv = _safe_pct_convert(df_adv, pct_cols)

                if daily_merged is None:
                    daily_merged = df_adv
                else:
                    daily_merged = pd.merge(daily_merged, df_adv, on='PLAYER_ID', how='outer')
            break

        except Exception as e:
            if getattr(e, "status_code", None) == 500 and is_full_refresh:
                _last_500_time = time.time()
            retry_wait = _retry_wait_with_edge_cooldown(e, attempt + 1, is_full_refresh=is_full_refresh)
            print(f"      Attempt {attempt + 1} failed for Advanced Stats on {date_str}: {type(e).__name__} ({e})")
            if attempt < fragile_max_retries - 1:
                time.sleep(retry_wait)
            else:
                print(f"      Fatal failure for Advanced Stats on {date_str}. Saving partial.")
                failed_endpoints.append("Advanced")
                break

    if daily_merged is not None:
        daily_merged['DATE_STR'] = date_str

    return daily_merged, failed_endpoints


def run_scrape(output_path=None, season=None):
    if season is None:
        season = os.environ.get("GAMELOGS_SEASON", SEASON)
    if output_path is None:
        output_path = os.environ.get("GAMELOGS_OUTPUT_PATH")
    if output_path is None:
        current_script_dir = os.path.dirname(os.path.abspath(__file__))
        backend_dir = os.path.dirname(current_script_dir)
        output_path = os.path.join(backend_dir, "data", "current", f"gamelogs_{season}.csv")
        print(f"      Warning: output_path not specified. Defaulting to {output_path}")

    print(f"   Managing Game Logs at {output_path}")

    # ------------------------------------------------------------------
    # Completed season guard: never incrementally update a finished season.
    # If the CSV already exists AND no failed manifest exists, we're done.
    # If a failed manifest exists, allow a recovery run.
    # If no CSV at all, do a one-time full fetch.
    # ------------------------------------------------------------------
    if season in COMPLETED_SEASONS:
        failed_manifest = output_path.replace(".csv", "_failed_dates.csv")
        if os.path.exists(output_path) and not os.path.exists(failed_manifest):
            print(f"   Season {season} is complete and data already exists. Nothing to do.")
            return _result("noop", f"Season {season} is complete and game logs are already present.")
        elif os.path.exists(failed_manifest):
            print(f"   Season {season} has unresolved failed dates. Attempting recovery...")
        else:
            print(f"   Season {season} is complete but no CSV found. Running one-time full fetch...")

    target_dates = []
    existing_df = pd.DataFrame()
    full_refresh = True
    completed_season_recovery_dates = None  # Only set for completed season manifest recovery

    if season not in COMPLETED_SEASONS and os.path.exists(output_path):
        try:
            print("      Found existing logs. Running INCREMENTAL update.")
            existing_df = pd.read_csv(output_path)
            if 'GAME_DATE' in existing_df.columns:
                existing_df['GAME_DATE'] = pd.to_datetime(existing_df['GAME_DATE'])
                existing_df['DATE_STR'] = existing_df['GAME_DATE'].dt.strftime('%m/%d/%Y')
            full_refresh = False
        except Exception as e:
            print(f"      Corrupt CSV ({e}). Forcing full refresh.")
            full_refresh = True
    elif season in COMPLETED_SEASONS and os.path.exists(output_path):
        # Load existing data to merge recovered dates into
        try:
            existing_df = pd.read_csv(output_path)
            if 'GAME_DATE' in existing_df.columns:
                existing_df['GAME_DATE'] = pd.to_datetime(existing_df['GAME_DATE'])
                existing_df['DATE_STR'] = existing_df['GAME_DATE'].dt.strftime('%m/%d/%Y')
        except Exception as e:
            print(f"      Could not load existing CSV for recovery ({e}). Starting fresh.")
        # Load the failed manifest and target only those dates
        failed_manifest = output_path.replace(".csv", "_failed_dates.csv")
        if os.path.exists(failed_manifest):
            try:
                failed_df = pd.read_csv(failed_manifest)
                if 'date' in failed_df.columns:
                    completed_season_recovery_dates = failed_df['date'].tolist()
                    full_refresh = False
                    print(f"      Targeting {len(completed_season_recovery_dates)} failed dates from manifest.")
            except Exception as e:
                print(f"      Could not read failed manifest ({e}). Falling back to full refresh.")

    df_logs = None
    for attempt in range(BASE_LOG_MAX_RETRIES):
        try:
            log_url = (
                f"https://stats.nba.com/stats/leaguegamelog"
                f"?Counter=0&DateFrom=&DateTo=&Direction=DESC&LeagueID=00"
                f"&PlayerOrTeam=P&Season={season}&SeasonType=Regular%20Season&Sorter=DATE"
            )

            r, route_label = fetch_nba_json(
                log_url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                attempt_index=attempt,
                context=f"Base LeagueGameLog for {season}",
            )

            if attempt > 0:
                print(f"   Base LeagueGameLog recovered on attempt {attempt + 1} via {route_label}.")

            api_headers = r['resultSets'][0].get('headers', [])
            row_set = r['resultSets'][0].get('rowSet', [])

            df_logs = pd.DataFrame(row_set, columns=api_headers)
            df_logs['GAME_DATE'] = pd.to_datetime(df_logs['GAME_DATE'])
            df_logs['DATE_STR'] = df_logs['GAME_DATE'].dt.strftime('%m/%d/%Y')
            break

        except Exception as e:
            print(f"   Base log attempt {attempt + 1} failed: {type(e).__name__} ({e})")
            if attempt < BASE_LOG_MAX_RETRIES - 1:
                time.sleep(_retry_wait_seconds(e, attempt + 1, is_full_refresh=True))

    if df_logs is None:
        print(f"   Fatal Error: Could not fetch base LeagueGameLog after {BASE_LOG_MAX_RETRIES} attempts.")
        last_saved_date = None
        if not existing_df.empty and 'GAME_DATE' in existing_df.columns:
            try:
                last_saved_date = existing_df['GAME_DATE'].max().strftime("%Y-%m-%d")
            except Exception:
                last_saved_date = None
        message = "Base LeagueGameLog could not be fetched after all retry attempts."
        if last_saved_date:
            message += f" Existing logs were preserved through {last_saved_date}."
        return _result("failed", message, last_saved_date=last_saved_date)

    all_active_dates = sorted(
        df_logs['DATE_STR'].unique().tolist(),
        key=lambda x: datetime.strptime(x, '%m/%d/%Y'),
        reverse=True
    )
    today_obj = datetime.now().date()
    valid_dates = [d for d in all_active_dates if datetime.strptime(d, '%m/%d/%Y').date() < today_obj]

    if full_refresh:
        target_dates = valid_dates
        workers = 1
        print(f"      Full Refresh (Reliability Mode): Fetching {len(target_dates)} dates with 1 worker.")
    elif completed_season_recovery_dates is not None:
        # Completed season: only re-fetch the specific dates that previously failed
        target_dates = [d for d in completed_season_recovery_dates if d in valid_dates]
        workers = MAX_WORKERS
        print(f"      Completed Season Recovery: Fetching {len(target_dates)} failed dates with {workers} workers.")
    else:
        cutoff_date = datetime.now() - timedelta(days=UPDATE_WINDOW_DAYS)
        target_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') >= cutoff_date]
        if not existing_df.empty:
            last_saved_date = existing_df['GAME_DATE'].max()
            missed_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') > last_saved_date]
            target_dates = list(set(target_dates + missed_dates))

            failed_manifest_path = output_path.replace(".csv", "_failed_dates.csv")
            if os.path.exists(failed_manifest_path):
                try:
                    failed_df = pd.read_csv(failed_manifest_path)
                    if 'date' in failed_df.columns:
                        manifest_dates = [d for d in failed_df['date'].tolist() if d in valid_dates]
                        target_dates = list(set(target_dates + manifest_dates))
                        print(f"      Included {len(manifest_dates)} previously failed dates from manifest for recovery.")
                except Exception as e:
                    print(f"      Could not load failed manifest: {e}")

        workers = MAX_WORKERS
        print(f"      Incremental: Scraping {len(target_dates)} dates with {workers} workers (Since {cutoff_date.strftime('%Y-%m-%d')})")

    if not target_dates:
        print("      Data is up to date (No completed games to fetch).")
        return _result("noop", "Game logs are already up to date.", target_dates=[])

    print(f"      Launching 1 worker sequentially for {len(target_dates)} dates using memory-safe incremental appending...")
    
    import gc

    advanced_data_list = []
    failed_dates = []
    none_dates = []
    dates_with_any_data = set()

    # THE MISSING LOOP: Process each date one by one
    for i, date_str in enumerate(target_dates):
        try:
            daily_merged, missing_endpoints = fetch_tracking_data_for_date(date_str, is_full_refresh=full_refresh, season=season)
            
            if daily_merged is not None:
                advanced_data_list.append(daily_merged)
                dates_with_any_data.add(date_str)
            
            if not missing_endpoints and daily_merged is not None:
                print(f"      [{i+1}/{len(target_dates)}] Completed {date_str}")
            elif daily_merged is None:
                print(f"      [{i+1}/{len(target_dates)}] NULL RESULT (all endpoints failed) {date_str}")
                none_dates.append(date_str)
            else:
                print(f"      [{i+1}/{len(target_dates)}] Partial {date_str} (Missing: {missing_endpoints})")
                failed_dates.append(date_str)
        except Exception as e:
            print(f"      Failed critically {date_str}: {e}")
            failed_dates.append(date_str)
            
        # Clean up RAM immediately after each day
        gc.collect()

    all_retry_dates = failed_dates + none_dates
    if none_dates:
        print(f"\n      Note: {len(none_dates)} dates returned None (all endpoints failed): {none_dates}")

    if all_retry_dates:
        print(f"\n      --- Commencing Retry for {len(all_retry_dates)} Failed/Null/Partial Dates ---")
        print("      Cooling down for 30 seconds before retrying to clear API rate limits...")
        time.sleep(30)

        permanently_failed = []

        for i, date_str in enumerate(all_retry_dates):
            try:
                print(f"      [RETRY {i+1}/{len(all_retry_dates)}] Fetching {date_str}...")
                daily_merged, missing_endpoints = fetch_tracking_data_for_date(date_str, is_full_refresh=True, season=season)
                
                if daily_merged is not None:
                    advanced_data_list.append(daily_merged)
                    dates_with_any_data.add(date_str)
                
                if not missing_endpoints and daily_merged is not None:
                    print(f"      [RETRY SUCCESS] {date_str}")
                elif daily_merged is None:
                    print(f"      [RETRY FATAL] {date_str} returned None again. Marking as permanently failed.")
                    permanently_failed.append({"date": date_str, "reason": "None after retry"})
                else:
                    print(f"      [RETRY PARTIAL] {date_str} is still missing: {missing_endpoints}")
                    permanently_failed.append({"date": date_str, "reason": f"Missing: {missing_endpoints}"})
            except Exception as e:
                print(f"      [RETRY FATAL] {date_str} is persistently failing: {e}")
                permanently_failed.append({"date": date_str, "reason": str(e)})

        if permanently_failed:
            failed_manifest_path = output_path.replace(".csv", "_failed_dates.csv")
            pd.DataFrame(permanently_failed).to_csv(failed_manifest_path, index=False)
            print(f"\n      {len(permanently_failed)} dates permanently failed (partial or complete).")
            print(f"      Manifest written to: {failed_manifest_path}")
            print(f"      Re-run the scraper with gamelogs.csv present — incremental mode will target only these gaps.")
        else:
            print(f"\n      All retried dates recovered successfully.")
            failed_manifest_path = output_path.replace(".csv", "_failed_dates.csv")
            if os.path.exists(failed_manifest_path):
                os.remove(failed_manifest_path)
                print("      Cleared the resolved failed manifest.")
    else:
        permanently_failed = []

    # THE MISSING MERGE & SAVE LOGIC
    if advanced_data_list:
        df_advanced_new = pd.concat(advanced_data_list, ignore_index=True)
        df_advanced_new = df_advanced_new.drop_duplicates(subset=['PLAYER_ID', 'DATE_STR'], keep='last')
        
        df_logs_filtered = df_logs[df_logs['DATE_STR'].isin(target_dates)]
        df_new_final = pd.merge(df_logs_filtered, df_advanced_new, on=['PLAYER_ID', 'DATE_STR'], how='left')

        core_cols_present = [c for c in CORE_FILL_ZERO_COLS if c in df_new_final.columns]
        df_new_final[core_cols_present] = df_new_final[core_cols_present].fillna(0)

        df_new_final['PTS+REB+AST'] = df_new_final['PTS'] + df_new_final['REB'] + df_new_final['AST']
        df_new_final['PTS+REB']     = df_new_final['PTS'] + df_new_final['REB']
        df_new_final['PTS+AST']     = df_new_final['PTS'] + df_new_final['AST']
        df_new_final['REB+AST']     = df_new_final['REB'] + df_new_final['AST']
        df_new_final['STL+BLK']     = df_new_final['STL'] + df_new_final['BLK']

        if not existing_df.empty:
            print(f"      Replacing overlapping data for {len(target_dates)} dates...")
            existing_df = existing_df[~existing_df['DATE_STR'].isin(target_dates)]
            final_df = pd.concat([existing_df, df_new_final], ignore_index=True)
        else:
            final_df = df_new_final
    else:
        final_df = existing_df

    if not final_df.empty:
        final_df['GAME_DATE'] = pd.to_datetime(final_df['GAME_DATE'])
        final_df = final_df.sort_values(by=['PLAYER_ID', 'GAME_DATE'], ascending=[True, False])
        final_df = final_df.groupby('PLAYER_ID').head(MAX_HISTORY_GAMES).reset_index(drop=True)

        cols_to_drop = ['SEASON_ID', 'PLAYER_NAME', 'TEAM_ID', 'TEAM_NAME', 'VIDEO_AVAILABLE']
        final_df = final_df.drop(columns=[c for c in cols_to_drop if c in final_df.columns])

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        final_df.to_csv(output_path, index=False)
        print(f"   Updated logs saved! (Rows: {len(final_df)})")

    if permanently_failed:
        if not dates_with_any_data:
            return _result(
                "failed",
                f"Game logs could not be refreshed for any of {len(target_dates)} target dates.",
                failed_dates=permanently_failed,
            )
        return _result(
            "partial",
            f"Game logs updated with partial coverage: {len(dates_with_any_data)}/{len(target_dates)} dates have data and {len(permanently_failed)} dates still have gaps.",
            failed_dates=permanently_failed,
            target_dates=target_dates,
        )

    return _result(
        "success",
        f"Game Logs Updated ({len(dates_with_any_data)}/{len(target_dates)} target dates).",
        target_dates=target_dates,
    )

if __name__ == "__main__":
    import sys
    # Usage:
    #   python gamelogs.py              -> runs 2025-26 (current season) as normal
    #   python gamelogs.py 2024-25      -> explicitly requests a completed season;
    #                                      fetches once if CSV missing, skips if already present
    cli_season = sys.argv[1] if len(sys.argv) > 1 else None

    # Safety: don't accidentally run a completed season without explicitly asking for it
    effective_season = cli_season or os.environ.get("GAMELOGS_SEASON", SEASON)
    if effective_season in COMPLETED_SEASONS and cli_season is None:
        print(f"   Season {effective_season} is in COMPLETED_SEASONS. "
              f"Pass it explicitly as an argument to fetch it: python gamelogs.py {effective_season}")
    else:
        env_path = os.environ.get("GAMELOGS_OUTPUT_PATH")
        run_scrape(output_path=env_path, season=effective_season)
