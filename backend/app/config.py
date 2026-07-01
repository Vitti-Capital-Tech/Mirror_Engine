from typing import List
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""   # for verifying user JWTs (Settings → API → JWT Secret)

    # Auth / 2FA (Resend email OTP)
    RESEND_API_KEY: str = ""
    RESEND_FROM: str = "Mirror Engine <noreply@example.com>"
    OTP_TTL_SECONDS: int = 300
    OTP_MAX_ATTEMPTS: int = 5

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Delta Exchange environment selector
    DELTA_ENV: str = "demo"  # 'demo' or 'live'

    # Delta Exchange Demo (Testnet) endpoints
    DELTA_DEMO_REST_URL: str = "https://cdn-ind.testnet.deltaex.org"
    DELTA_DEMO_WS_URL: str = "wss://socket-ind.testnet.deltaex.org"

    # Delta Exchange Live (Production) endpoints
    DELTA_LIVE_REST_URL: str = "https://api.india.delta.exchange"
    DELTA_LIVE_WS_URL: str = "wss://socket.india.delta.exchange"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://localhost:8080"

    @field_validator("DELTA_ENV")
    @classmethod
    def validate_delta_env(cls, v: str) -> str:
        if v not in ("demo", "live"):
            raise ValueError("DELTA_ENV must be 'demo' or 'live'")
        return v

    @property
    def delta_rest_url(self) -> str:
        """Return the REST URL for the configured Delta Exchange environment."""
        return self.DELTA_DEMO_REST_URL if self.DELTA_ENV == "demo" else self.DELTA_LIVE_REST_URL

    @property
    def delta_ws_url(self) -> str:
        """Return the WebSocket URL for the configured Delta Exchange environment."""
        return self.DELTA_DEMO_WS_URL if self.DELTA_ENV == "demo" else self.DELTA_LIVE_WS_URL

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS string into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
