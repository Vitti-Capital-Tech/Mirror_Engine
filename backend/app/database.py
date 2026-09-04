import logging
import httpx
from supabase import create_client, Client
from app.config import settings

logger = logging.getLogger(__name__)


def _force_http11(client: Client) -> None:
    """Serve Supabase over HTTP/1.1 instead of HTTP/2.

    supabase-py 2.4.6 enables HTTP/2 on the PostgREST session, and a long-lived
    HTTP/2 connection is capped on how many streams it will carry. When the cap is
    reached the server sends a graceful GOAWAY, and every request already dispatched
    onto that connection dies:

        ConnectionTerminated error_code:0, last_stream_id:19999

    Observed 11 times on 2026-09-04, and each one broke something real and silent —
    "Error handling position update", "Error checking position sync", "Failed to
    sync live positions for account Jigar", "Failed to read accounts". error_code 0
    is NO_ERROR: nothing is wrong, the connection is simply used up.

    HTTP/2's only advantage here is multiplexing concurrent requests, and this
    client is SYNCHRONOUS — every call blocks the event loop for its round trip
    (see copy_engine._read_accounts), so requests are serialised and there is
    nothing to multiplex. HTTP/1.1 with keep-alive is therefore free of cost, and
    it has no stream cap to exhaust.

    retries=2 covers the other way a pooled connection dies: the server closing an
    idle keep-alive socket between requests. httpx retries those at connect time.

    Reaches into a private attribute because ClientOptions in 2.4.6 exposes no way
    to supply a transport. Wrapped so that a supabase-py internals change degrades
    to today's behaviour rather than refusing to start.
    """
    try:
        session = client.postgrest.session
        session._transport = httpx.HTTPTransport(http2=False, retries=2)
        logger.info("Supabase PostgREST session pinned to HTTP/1.1 (no stream cap)")
    except Exception as exc:
        logger.warning(
            "Could not pin Supabase to HTTP/1.1 (%s) — staying on the default "
            "transport; expect occasional ConnectionTerminated", exc,
        )

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
            _force_http11(_db_client)
            logger.info("Supabase client initialised successfully.")
        except Exception as exc:
            logger.error("Failed to initialise Supabase client: %s", exc)
            raise RuntimeError(f"Supabase initialisation failed: {exc}") from exc
    return _db_client


# Module-level singleton — import `db` for convenience
db: Client = get_db()
