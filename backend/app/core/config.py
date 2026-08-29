from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


AIProvider = Literal["local", "azure", "hybrid"]
MediaStorageProvider = Literal["local", "azure_blob"]
PhoneOTPProvider = Literal["disabled", "console", "acs"]
Environment = Literal["development", "test", "production"]

DatabaseSSLMode = Literal[
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
]


class Settings(BaseSettings):
    # Application
    PROJECT_NAME: str = "Tat-Sahayk API"
    ENVIRONMENT: Environment = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 5001
    API_V1_PREFIX: str = "/api/v1"

    # Security
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Database
    DATABASE_URL: str
    DATABASE_SSL_MODE: DatabaseSSLMode = "disable"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # CORS
    CORS_ORIGINS: str = (
        "http://localhost:5173,http://localhost:5174"
    )

    # AI provider
    AI_PROVIDER: AIProvider = "local"
    AI_FALLBACK_ENABLED: bool = False

    # Local ML service
    ML_SERVICE_URL: str = "http://localhost:8000"
    ML_SERVICE_TIMEOUT_SECONDS: float = 30.0
    ML_SERVICE_HEALTH_PATH: str = "/health"
    ML_SERVICE_ANALYZE_PATH: str = (
        "/api/v1/analyze/report"
    )

    # Media storage
    MEDIA_STORAGE_PROVIDER: MediaStorageProvider = "local"
    LOCAL_MEDIA_DIR: str = "uploads"
    LOCAL_MEDIA_URL: str = "/uploads"
    MEDIA_MAX_FILE_SIZE_MB: int = 10
    MEDIA_ALLOWED_CONTENT_TYPES: str = (
        "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/quicktime"
    )

    # Phone verification
    PHONE_OTP_PROVIDER: PhoneOTPProvider = "disabled"
    PHONE_OTP_TTL_MINUTES: int = Field(
        default=10,
        ge=1,
        le=30,
    )
    PHONE_OTP_RESEND_SECONDS: int = Field(
        default=60,
        ge=0,
        le=3600,
    )
    PHONE_OTP_MAX_ATTEMPTS: int = Field(
        default=5,
        ge=1,
        le=10,
    )

    # Background processing
    ENABLE_SOCIAL_HARVESTER: bool = False
    ENABLE_CLUSTER_ANALYSIS: bool = False
    SOCIAL_HARVEST_INTERVAL_MINUTES: int = 15
    CLUSTER_ANALYSIS_INTERVAL_MINUTES: int = 15

    # Azure
    AZURE_ENABLED: bool = False

    # Azure OpenAI (Microsoft Foundry). One gpt-4o-mini
    # deployment serves both the vision and text paths, so
    # both deployment settings normally hold the same name.
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_API_KEY: Optional[str] = None
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"
    AZURE_OPENAI_VISION_DEPLOYMENT: str = "analysis"
    AZURE_OPENAI_TEXT_DEPLOYMENT: str = "analysis"

    # Azure Blob Storage
    AZURE_STORAGE_CONNECTION_STRING: Optional[str] = None
    AZURE_STORAGE_ACCOUNT: Optional[str] = None
    AZURE_STORAGE_CONTAINER: str = "report-media"

    # Azure Communication Services
    ACS_CONNECTION_STRING: Optional[str] = None
    ACS_SENDER_EMAIL: Optional[str] = None
    ACS_SMS_FROM: Optional[str] = None

    # Alert fan-out batching. Azure Communication Services
    # accepts at most 50 recipients per message, and an
    # Azure-managed domain is capped at 10 messages per hour
    # with no way to raise it, so alerts are batched into a
    # small number of BCC messages rather than one per user.
    ACS_EMAIL_RECIPIENTS_PER_MESSAGE: int = Field(
        default=50,
        ge=1,
        le=50,
    )
    ACS_EMAIL_MAX_MESSAGES_PER_ALERT: int = Field(
        default=2,
        ge=1,
        le=10,
    )

    # Azure AI Video Indexer. Billed per input minute and
    # unusable with a free trial account, so it is opt-in
    # separately from AZURE_ENABLED.
    AZURE_VIDEO_INDEXER_ENABLED: bool = False
    AZURE_VIDEO_INDEXER_ACCOUNT_ID: Optional[str] = None
    AZURE_VIDEO_INDEXER_LOCATION: Optional[str] = None
    AZURE_VIDEO_INDEXER_API_KEY: Optional[str] = None
    AZURE_VIDEO_INDEXER_TIMEOUT_SECONDS: int = Field(
        default=300,
        ge=30,
        le=900,
    )

    # Presets keep the bill to visual insights only: no audio
    # transcription, no encoding for playback. The accepted enum
    # values are only published in the Video Indexer API portal,
    # which needs an account to read. If an upload returns HTTP
    # 400 naming one of these, confirm the value for your account
    # and correct it, or blank it out to fall back to the account
    # default (which costs more).
    AZURE_VIDEO_INDEXER_INDEXING_PRESET: str = "VideoOnly"
    AZURE_VIDEO_INDEXER_STREAMING_PRESET: str = "NoStreaming"

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None

    # External verification services
    OPENWEATHER_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_provider_configuration(self):
        if (
            self.ENVIRONMENT == "production"
            and self.PHONE_OTP_PROVIDER == "console"
        ):
            raise ValueError(
                "PHONE_OTP_PROVIDER=console is not allowed "
                "in production"
            )

        if self.PHONE_OTP_PROVIDER == "acs":
            if not self.AZURE_ENABLED:
                raise ValueError(
                    "AZURE_ENABLED must be true when "
                    "PHONE_OTP_PROVIDER=acs"
                )

            if not self.ACS_CONNECTION_STRING:
                raise ValueError(
                    "ACS_CONNECTION_STRING is required when "
                    "PHONE_OTP_PROVIDER=acs"
                )

            if not self.ACS_SMS_FROM:
                raise ValueError(
                    "ACS_SMS_FROM is required when "
                    "PHONE_OTP_PROVIDER=acs"
                )

        if self.uses_azure_ai and not self.azure_openai_configured:
            raise ValueError(
                "AZURE_ENABLED, AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY are required when "
                f"AI_PROVIDER={self.AI_PROVIDER}"
            )

        if (
            self.AI_PROVIDER == "local"
            and self.AI_FALLBACK_ENABLED
            and not self.azure_openai_configured
        ):
            raise ValueError(
                "AZURE_ENABLED, AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY are required when "
                "AI_FALLBACK_ENABLED=true"
            )

        if (
            self.MEDIA_STORAGE_PROVIDER == "azure_blob"
            and not self.azure_blob_configured
        ):
            raise ValueError(
                "AZURE_ENABLED and "
                "AZURE_STORAGE_CONNECTION_STRING are required "
                "when MEDIA_STORAGE_PROVIDER=azure_blob"
            )

        if self.AZURE_VIDEO_INDEXER_ENABLED:
            if not self.video_indexer_configured:
                raise ValueError(
                    "AZURE_ENABLED, "
                    "AZURE_VIDEO_INDEXER_ACCOUNT_ID, "
                    "AZURE_VIDEO_INDEXER_LOCATION and "
                    "AZURE_VIDEO_INDEXER_API_KEY are required "
                    "when AZURE_VIDEO_INDEXER_ENABLED=true"
                )

            # Video Indexer downloads the video over the public
            # internet by URL. Local storage returns a relative
            # /uploads path, which it cannot resolve.
            if self.MEDIA_STORAGE_PROVIDER != "azure_blob":
                raise ValueError(
                    "MEDIA_STORAGE_PROVIDER=azure_blob is "
                    "required when "
                    "AZURE_VIDEO_INDEXER_ENABLED=true"
                )

        return self

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def uses_local_ml(self) -> bool:
        return self.AI_PROVIDER in {"local", "hybrid"}

    @property
    def uses_azure_ai(self) -> bool:
        return self.AI_PROVIDER in {"azure", "hybrid"}

    @property
    def uses_local_media(self) -> bool:
        return self.MEDIA_STORAGE_PROVIDER == "local"

    @property
    def local_media_directory(self) -> Path:
        return Path(
            self.LOCAL_MEDIA_DIR
        ).expanduser().resolve()

    @property
    def local_media_url(self) -> str:
        normalized = self.LOCAL_MEDIA_URL.strip().strip("/")

        if not normalized:
            return "/uploads"

        return f"/{normalized}"

    @property
    def media_max_file_size_bytes(self) -> int:
        return self.MEDIA_MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def media_allowed_content_types(self) -> frozenset[str]:
        return frozenset(
            content_type.strip().lower()
            for content_type in (
                self.MEDIA_ALLOWED_CONTENT_TYPES.split(",")
            )
            if content_type.strip()
        )

    @property
    def azure_openai_configured(self) -> bool:
        return bool(
            self.AZURE_ENABLED
            and self.AZURE_OPENAI_ENDPOINT
            and self.AZURE_OPENAI_API_KEY
        )

    @property
    def azure_blob_configured(self) -> bool:
        return bool(
            self.AZURE_ENABLED
            and self.AZURE_STORAGE_CONNECTION_STRING
            and self.AZURE_STORAGE_CONTAINER
        )

    @property
    def acs_configured(self) -> bool:
        return bool(
            self.AZURE_ENABLED
            and self.ACS_CONNECTION_STRING
        )

    @property
    def acs_email_configured(self) -> bool:
        return bool(
            self.acs_configured
            and self.ACS_SENDER_EMAIL
        )

    @property
    def video_indexer_configured(self) -> bool:
        return bool(
            self.AZURE_VIDEO_INDEXER_ENABLED
            and self.AZURE_ENABLED
            and self.AZURE_VIDEO_INDEXER_ACCOUNT_ID
            and self.AZURE_VIDEO_INDEXER_LOCATION
            and self.AZURE_VIDEO_INDEXER_API_KEY
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
