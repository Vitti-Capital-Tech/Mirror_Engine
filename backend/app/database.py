import logging
from supabase import create_client, Client
from app.config import settings

logger = logging.getLogger(__name__)

_db_client: Client | None = None


def get_db() -> Client:
    """
    Return the singleton Supabase client, creating it on first call.
    Raises RuntimeError if the client cannot be initialised.
    """
    global _db_client
    if _db_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment."
            )
        try:
            _db_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
            logger.info("Supabase client initialised successfully.")
        except Exception as exc:
            logger.error("Failed to initialise Supabase client: %s", exc)
            raise RuntimeError(f"Supabase initialisation failed: {exc}") from exc
    return _db_client


# Module-level singleton — import `db` for convenience
db: Client = get_db()
