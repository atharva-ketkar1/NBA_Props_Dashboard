"""
cron_state_store.py
Durable storage for master_cron's scheduler state.

Why this exists: the state used to live only in backend/logs/master_cron_state.json.
The VM is disposable (and one has already been lost), so a rebuilt box would forget
whether it had run today's pipeline, which closing lines it had captured, and how many
failures it had burned. Postgres outlives the VM, so the state belongs there.

Design notes:
  - Supabase is the source of truth; the local JSON file is kept in sync as a cache so
    a Supabase outage degrades to the old behaviour instead of losing the schedule.
  - Reads prefer Supabase, fall back to the file. Writes go to both; a failed DB write
    is logged loudly but does not crash the scheduler, because dropping a cron tick is
    worse than running on slightly stale state.
  - If the cron_state table does not exist yet (migration not applied), this silently
    degrades to file-only and says so once, so deploying the code before the migration
    is safe.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

STATE_KEY = "master_cron"
TABLE = "cron_state"

# Set CRON_STATE_BACKEND=file to pin to the old file-only behaviour.
_BACKEND = os.environ.get("CRON_STATE_BACKEND", "auto").strip().lower()

_table_missing_warned = False


def _db_enabled():
    if _BACKEND == "file":
        return False
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _client():
    from utils.supabase_client import get_supabase_client
    return get_supabase_client()


def _is_missing_table(exc):
    text = str(exc).lower()
    # PostgREST reports an unknown relation as PGRST205 / "could not find the table".
    return "pgrst205" in text or "could not find the table" in text or "does not exist" in text


def _warn_table_missing_once():
    global _table_missing_warned
    if not _table_missing_warned:
        _table_missing_warned = True
        logger.warning(
            "cron_state table not found - falling back to local file. "
            "Apply supabase/migrations/20261019_cron_state.sql to make scheduler state survive the VM."
        )


def load_state(state_file, default_state):
    """
    Return the scheduler state, preferring Supabase and falling back to `state_file`.
    Always returns default_state merged with whatever was found.
    """
    if _db_enabled():
        try:
            resp = _client().table(TABLE).select("state").eq("key", STATE_KEY).limit(1).execute()
            rows = getattr(resp, "data", None) or []
            if rows:
                stored = rows[0].get("state")
                if isinstance(stored, str):
                    stored = json.loads(stored)
                if isinstance(stored, dict):
                    logger.debug("Loaded cron state from Supabase")
                    return {**default_state, **stored}
            # No row yet: fall through to the file so an existing VM keeps its place
            # on first upgrade, then the next save seeds the table.
        except Exception as exc:
            if _is_missing_table(exc):
                _warn_table_missing_once()
            else:
                logger.error("Could not read cron state from Supabase, using local file: %s", exc)

    if state_file and os.path.exists(state_file):
        try:
            with open(state_file, "r") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                return {**default_state, **raw}
        except Exception as exc:
            logger.error("Error loading local cron state: %s", exc)

    return dict(default_state)


def save_state(state_file, state):
    """
    Persist `state` to Supabase and to `state_file`. Returns True if the durable
    (Supabase) write succeeded, so callers can tell whether the VM is now replaceable.
    """
    durable = False
    if _db_enabled():
        try:
            _client().table(TABLE).upsert(
                {"key": STATE_KEY, "state": state},
                on_conflict="key",
            ).execute()
            durable = True
        except Exception as exc:
            if _is_missing_table(exc):
                _warn_table_missing_once()
            else:
                logger.error("Could not persist cron state to Supabase: %s", exc)

    if state_file:
        try:
            with open(state_file, "w") as handle:
                json.dump(state, handle, indent=4)
        except Exception as exc:
            logger.error("Error saving local cron state: %s", exc)

    return durable
