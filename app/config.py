import enum
from functools import lru_cache
from typing import List, Optional, Union

from pydantic import AnyHttpUrl, DirectoryPath, HttpUrl, field_validator
from pydantic_core.core_schema import FieldValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL
from sqlalchemy.engine import make_url
from structlog import get_logger

logger = get_logger()


class Settings(BaseSettings):
    API_V1_STR: str = "/v1"

    FIREBASE_PROJECT_ID: str = "wriveted-api"

    # # TODO these should be optional, we could support deployment on AWS (e.g. RDS + Fargate)
    # # GCP specific configuration
    GCP_PROJECT_ID: str = "wriveted-api"
    GCP_CLOUD_SQL_INSTANCE_ID: str = "wriveted"
    GCP_LOCATION: str = "australia-southeast1"

    GCP_IMAGE_BUCKET: str = "wriveted-cover-images"
    GCP_HUEY_MEDIA_BUCKET: str = "wriveted-huey-media"
    GCP_BOOK_DATA_BUCKET: str = "wriveted-book-data"

    GOOGLE_API_KEY: str = ""
    GOOGLE_CSE_ID: str = ""

    GCP_CLOUD_TASKS_NAME: Optional[str] = None  # 'background-tasks'
    GCP_CLOUD_TASKS_SERVICE_ACCOUNT: str = (
        "background-tasks@wriveted-api.iam.gserviceaccount.com"
    )
    WRIVETED_INTERNAL_API: Optional[AnyHttpUrl] = None

    POSTGRESQL_DATABASE_SOCKET_PATH: Optional[DirectoryPath] = None  # e.g. /cloudsql

    POSTGRESQL_SERVER: str = "/"
    POSTGRESQL_DATABASE: str = "postgres"
    POSTGRESQL_USER: str = "postgres"
    POSTGRESQL_PASSWORD: str
    POSTGRESQL_PORT: int = 5432

    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 10

    SENDGRID_API_KEY: str

    SHOPIFY_HMAC_SECRET: str

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5-mini"
    OPENAI_TIMEOUT: float = 60.0

    # "openai" or "gemini"
    LABELLING_PROVIDER: str = "openai"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3-flash-preview"

    LABEL_AFTER_HYDRATION: bool = True

    GPT_SERVICE_ACCOUNT_ID: str = "2c092595-76e8-4ba9-9340-37b7237d6b59"

    SLACK_BOT_TOKEN: str = ""

    HUEY_BOOKS_APP_URL: str = "https://hueybooks.com"

    NIELSEN_API_URL: str = (
        "https://ws.nielsenbookdataonline.com/BDOLRest/RESTwebServices/BDOLrequest"
    )
    NIELSEN_CLIENT_ID: str = "WrivetedWebServices"
    NIELSEN_PASSWORD: str = ""
    NIELSEN_ENABLE_CACHE: bool = False
    NIELSEN_CACHE_RESULTS: bool = True

    SQLALCHEMY_DRIVERNAME: str = "postgresql+psycopg2"
    SQLALCHEMY_DATABASE_URI: URL | None = None

    @field_validator("SQLALCHEMY_DATABASE_URI", mode="before")
    @classmethod
    def assemble_sqlalchemy_connection(
        cls, v: Optional[str], info: FieldValidationInfo
    ) -> URL:
        if isinstance(v, str):
            # If a string is provided (e.g. via environment variable) we just use that
            return make_url(v)

        values = info.data
        # Otherwise, assemble a sqlalchemy connection string from the other provided values.
        db_host = values.get("POSTGRESQL_SERVER")
        db_user = values.get("POSTGRESQL_USER")
        db_password = values.get("POSTGRESQL_PASSWORD")
        db_name = values.get("POSTGRESQL_DATABASE")
        db_port = values.get("POSTGRESQL_PORT")

        logger.debug(f"Database host {db_host}. DB name {db_name}")

        query = None
        # Connect to Cloud SQL using unix socket instead of TCP socket
        # https://cloud.google.com/sql/docs/postgres/connect-run?authuser=1#connecting_to
        socket_path = values.get("POSTGRESQL_DATABASE_SOCKET_PATH")

        if socket_path is not None:
            project = values.get("GCP_PROJECT_ID")
            location = values.get("GCP_LOCATION")
            cloud_sql_instance_id = values.get("GCP_CLOUD_SQL_INSTANCE_ID")
            cloud_sql_instance_connection = (
                f"{project}:{location}:{cloud_sql_instance_id}"
            )
            query = {"host": f"{socket_path}/{cloud_sql_instance_connection}"}
            db_host = None
            db_port = None

        # Assemble it all together:
        url_object = URL.create(
            values.get("SQLALCHEMY_DRIVERNAME"),
            username=db_user,
            password=db_password,
            host=db_host,
            port=db_port,
            database=db_name,
            query=query,
        )
        logger.debug(f"sqlalchemy connection URL {url_object}")
        return url_object

    SQLALCHEMY_ASYNC_URI: URL | None = None

    @field_validator("SQLALCHEMY_ASYNC_URI", mode="before")
    @classmethod
    def assemble_async_sqlalchemy_connection(
        cls, v: Optional[str], info: FieldValidationInfo
    ) -> URL:
        if isinstance(v, str):
            # If a string is provided (e.g. via environment variable) we just use that
            return make_url(v)

        values = info.data
        # Otherwise, assemble a sqlalchemy connection string from the other provided values.
        db_host = values.get("POSTGRESQL_SERVER")
        db_user = values.get("POSTGRESQL_USER")
        db_password = values.get("POSTGRESQL_PASSWORD")
        db_name = values.get("POSTGRESQL_DATABASE")
        db_port = values.get("POSTGRESQL_PORT")

        logger.debug(f"Database host {db_host}. DB name {db_name}")

        query = None
        # Connect to Cloud SQL using unix socket instead of TCP socket
        # https://cloud.google.com/sql/docs/postgres/connect-run?authuser=1#connecting_to
        socket_path = values.get("POSTGRESQL_DATABASE_SOCKET_PATH")

        if socket_path is not None:
            project = values.get("GCP_PROJECT_ID")
            location = values.get("GCP_LOCATION")
            cloud_sql_instance_id = values.get("GCP_CLOUD_SQL_INSTANCE_ID")
            cloud_sql_instance_connection = (
                f"{project}:{location}:{cloud_sql_instance_id}"
            )
            query = {"host": f"{socket_path}/{cloud_sql_instance_connection}"}
            db_host = None
            db_port = None

        # Assemble it all together:
        url_object = URL.create(
            "postgresql+asyncpg",
            username=db_user,
            password=db_password,
            host=db_host,
            port=db_port,
            database=db_name,
            query=query,
        )
        logger.debug(f"sqlalchemy async connection URL {url_object}")
        return url_object

    # BACKEND_CORS_ORIGINS is a JSON-formatted list of allowed request origins
    # e.g: '["http://localhost", "http://localhost:4200", "http://localhost:3000", \
    # "http://localhost:8080", "http://actual.domain.com"]'
    BACKEND_CORS_ORIGINS: List[str] = [
        # Local development URLs
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3005",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        # Production URLs
        "https://api.wriveted.com",
        "https://bookbot.hellohuey.com",
        "https://app.hueythebookbot.com",
        "https://api.hueythebookbot.com",
        "https://www.hueythebookbot.com",
        # Huey Books Production URLs
        "https://hueybooks.com",
        "https://www.hueybooks.com",
        # Firebase URLs
        "https://wriveted-library.web.app",
        "https://wriveted-api.web.app",
        "https://huey-books.web.app",
        # Production Cloud Run Deployments - Direct URLs
        "https://wriveted-api-lg5ntws4da-ts.a.run.app",
        "https://wriveted-admin-ui-lg5ntws4da-ts.a.run.app",
        # Non Prod Cloud Run Deployment - Direct URLs
        "https://wriveted-api--nonprod-main-3glz1j0b.web.app",
        # Landbot
        "http://34.77.31.159",
        "http://23.251.142.192",
        "https://chats.landbot.io",
        "https://landbot.site",
        "https://landbot.pro",
        # TypeBot
        "https://typebot.io",
        "http://13.38.101.232",
        "http://15.188.52.37",
    ]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    # This must be set for JWT token to persist and be valid between multiple
    # API processes create with `secrets.token_urlsafe(32)`
    SECRET_KEY: str

    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8

    # 2 years
    SERVICE_ACCOUNT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 365 * 2

    class LoggingLevel(str, enum.Enum):
        DEBUG = "DEBUG"
        INFO = "INFO"
        WARNING = "WARNING"

    LOGGING_LEVEL: LoggingLevel = LoggingLevel.INFO
    SQLALCHEMY_LOGGING_LEVEL: LoggingLevel = LoggingLevel.WARNING
    AUTH_LOGGING_LEVEL: LoggingLevel = LoggingLevel.INFO

    METABASE_SITE_URL: HttpUrl = "https://metabase-lg5ntws4da-ts.a.run.app"
    METABASE_SECRET_KEY: str = ""

    # Capture uvicorn's access log messages in our logging stack
    LOG_UVICORN_ACCESS: bool = True
    LOG_AS_JSON: bool = False
    DEBUG: bool = False

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    ENABLE_OTEL_GOOGLE_EXPORTER: bool = False

    # E2E Test configuration - when set, enables test auth endpoints
    # This should NEVER be set in production
    E2E_TEST_AUTH_SECRET: Optional[str] = None

    # Disable event listener for tests to prevent connection pool exhaustion
    # Set to True in test environment to skip PostgreSQL LISTEN/NOTIFY
    DISABLE_EVENT_LISTENER: bool = False

    # Disable CSRF cookie validation for cross-origin development
    # When True, only validates the X-CSRF-Token header, not the cookie
    # This should only be enabled in development/debug mode
    CSRF_SKIP_COOKIE_VALIDATION: bool = False

    model_config = SettingsConfigDict(case_sensitive=True, use_enum_values=True)


@lru_cache()
def get_settings() -> Settings:
    return Settings()
