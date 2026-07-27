from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


AIProvider = Literal["local", "bedrock", "hybrid"]
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
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174"

    # AI provider selection
    AI_PROVIDER: AIProvider = "local"
    AI_FALLBACK_ENABLED: bool = False

    # Local ML service
    ML_SERVICE_URL: str = "http://localhost:8000"
    ML_SERVICE_TIMEOUT_SECONDS: float = 30.0
    ML_SERVICE_HEALTH_PATH: str = "/health"
    ML_SERVICE_ANALYZE_PATH: str = "/api/v1/analyze/report"

    # Background processing
    ENABLE_SOCIAL_HARVESTER: bool = False
    ENABLE_CLUSTER_ANALYSIS: bool = False
    SOCIAL_HARVEST_INTERVAL_MINUTES: int = 15
    CLUSTER_ANALYSIS_INTERVAL_MINUTES: int = 15

    # AWS
    AWS_ENABLED: bool = False
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    S3_BUCKET: Optional[str] = None
    AWS_BEDROCK_MODEL_ID: str = "us.amazon.nova-pro-v1:0"
    AWS_BEDROCK_TEXT_MODEL_ID: str = "us.amazon.nova-micro-v1:0"
    SES_SOURCE_EMAIL: Optional[str] = None

    # Google OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None

    # External verification services
    OPENWEATHER_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None

    # Legacy Cloudinary configuration
    CLOUDINARY_CLOUD_NAME: Optional[str] = None
    CLOUDINARY_API_KEY: Optional[str] = None
    CLOUDINARY_API_SECRET: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        """Return normalized CORS origins from the comma-separated setting."""
        return [
            origin.strip().rstrip("/")
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def uses_local_ml(self) -> bool:
        return self.AI_PROVIDER in {"local", "hybrid"}

    @property
    def uses_bedrock(self) -> bool:
        return self.AI_PROVIDER in {"bedrock", "hybrid"}

    @property
    def aws_credentials_configured(self) -> bool:
        return bool(
            self.AWS_ENABLED
            and self.AWS_ACCESS_KEY_ID
            and self.AWS_SECRET_ACCESS_KEY
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()