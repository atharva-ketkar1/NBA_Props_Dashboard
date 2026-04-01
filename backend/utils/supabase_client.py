"""
supabase_client.py
Shared Supabase client singleton for all backend write operations.
Reads credentials from environment variables only — never hardcoded.

Usage:
    from utils.supabase_client import get_supabase_client
    sb = get_supabase_client()
    sb.table('players').upsert(rows).execute()
"""

import os
import logging
from functools import lru_cache
from dotenv import load_dotenv

# Load .env from the backend directory (one level up from utils/)
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_supabase_client():
    """
    Returns a cached Supabase client using the service_role key.
    The service_role key bypasses RLS — only use on the backend/VM.
    NEVER expose SUPABASE_SERVICE_KEY to the frontend.
    """
    from supabase import create_client, Client

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

    if not url or not key:
        raise EnvironmentError(
            "Missing Supabase credentials. Set these environment variables:\n"
            "  export SUPABASE_URL='https://YOUR_PROJECT_ID.supabase.co'\n"
            "  export SUPABASE_SERVICE_KEY='eyJhbGc...your_service_role_key...'\n"
            "Or add them to backend/.env"
        )

    logger.debug("Initializing Supabase client for %s", url)
    return create_client(url, key)
