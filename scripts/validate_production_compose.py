#!/usr/bin/env python3
"""Validate the rendered Tat-Sahayk production Compose configuration."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


EXPECTED_SERVICES = {"db", "ml-service", "backend", "frontend"}
REQUIRED_VOLUMES = {
    "postgres_data",
    "uploads_data",
    "huggingface_cache",
    "nltk_cache",
}
PLACEHOLDER_MARKERS = {
    "change-me",
    "changeme",
    "example",
    "replace-with",
    "your-secret",
}
# Checked for placeholders only when AZURE_ENABLED is true.
AZURE_SECRET_VARIABLES = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_STORAGE_CONNECTION_STRING",
    "ACS_CONNECTION_STRING",
    "ACS_SENDER_EMAIL",
    "AZURE_VIDEO_INDEXER_API_KEY",
)


def require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def command_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value or "")


def has_strong_secret(value: str, minimum_length: int) -> bool:
    normalized = value.strip().lower()
    return len(value.strip()) >= minimum_length and not any(
        marker in normalized for marker in PLACEHOLDER_MARKERS
    )


def load_rendered_config(
    repository_root: Path,
    compose_file: Path,
    env_file: Path,
) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_file),
        "config",
        "--format",
        "json",
    ]

    try:
        result = subprocess.run(
            command,
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Docker Compose is required for production validation"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(
            f"Docker Compose configuration failed: {detail}"
        ) from exc

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Docker Compose returned invalid JSON"
        ) from exc


def validate_config(
    config: dict[str, Any],
    *,
    allow_example_secrets: bool,
) -> list[str]:
    errors: list[str] = []
    services = config.get("services", {})
    volumes = config.get("volumes", {})

    require(
        errors,
        set(services) == EXPECTED_SERVICES,
        "production services must be exactly: "
        + ", ".join(sorted(EXPECTED_SERVICES)),
    )
    require(
        errors,
        REQUIRED_VOLUMES.issubset(volumes),
        "missing required named volumes: "
        + ", ".join(sorted(REQUIRED_VOLUMES - set(volumes))),
    )

    for service_name, service in services.items():
        require(
            errors,
            service.get("restart") in {"always", "unless-stopped"},
            f"{service_name}: restart policy is required",
        )
        healthcheck = service.get("healthcheck", {})
        require(
            errors,
            bool(healthcheck) and not healthcheck.get("disable"),
            f"{service_name}: an enabled health check is required",
        )
        require(
            errors,
            not service.get("container_name"),
            f"{service_name}: fixed container_name is not allowed",
        )
        require(
            errors,
            not service.get("privileged", False),
            f"{service_name}: privileged mode is not allowed",
        )
        require(
            errors,
            service.get("network_mode") != "host",
            f"{service_name}: host networking is not allowed",
        )

        logging = service.get("logging", {})
        logging_options = logging.get("options", {})
        require(
            errors,
            logging.get("driver") == "json-file"
            and bool(logging_options.get("max-size"))
            and bool(logging_options.get("max-file")),
            f"{service_name}: bounded json-file logging is required",
        )

        runtime_command = " ".join(
            (
                command_text(service.get("command")),
                command_text(service.get("entrypoint")),
            )
        )
        require(
            errors,
            "--reload" not in runtime_command,
            f"{service_name}: development reload is not allowed",
        )

        for mount in service.get("volumes", []):
            require(
                errors,
                mount.get("type") != "bind",
                f"{service_name}: bind mounts are not allowed",
            )

        ports = service.get("ports", [])
        if service_name == "frontend":
            require(
                errors,
                len(ports) == 1
                and ports[0].get("target") == 80
                and bool(ports[0].get("published")),
                "frontend: exactly one published port targeting 80 is required",
            )
        else:
            require(
                errors,
                not ports,
                f"{service_name}: host ports must not be published",
            )

    backend = services.get("backend", {})
    ml_service = services.get("ml-service", {})
    database = services.get("db", {})
    frontend = services.get("frontend", {})
    backend_environment = backend.get("environment", {})
    ml_environment = ml_service.get("environment", {})

    expected_environment = (
        ("backend", backend_environment),
        ("ml-service", ml_environment),
    )
    for service_name, environment in expected_environment:
        require(
            errors,
            environment.get("ENVIRONMENT") == "production",
            f"{service_name}: ENVIRONMENT must be production",
        )
        require(
            errors,
            environment.get("DEBUG") == "false",
            f"{service_name}: DEBUG must be false",
        )

    install_dev = (
        backend.get("build", {}).get("args", {}).get("INSTALL_DEV")
    )
    require(
        errors,
        install_dev == "false",
        "backend: INSTALL_DEV build argument must be false",
    )
    require(
        errors,
        backend_environment.get("ML_SERVICE_URL")
        == "http://ml-service:8000",
        "backend: ML_SERVICE_URL must use the internal service name",
    )

    backend_dependencies = backend.get("depends_on", {})
    for dependency in ("db", "ml-service"):
        condition = backend_dependencies.get(dependency, {}).get("condition")
        require(
            errors,
            condition == "service_healthy",
            f"backend: '{dependency}' must require service_healthy",
        )

    frontend_condition = (
        frontend.get("depends_on", {})
        .get("backend", {})
        .get("condition")
    )
    require(
        errors,
        frontend_condition == "service_healthy",
        "frontend: backend must require service_healthy",
    )

    expected_mounts = {
        ("db", "postgres_data", "/var/lib/postgresql/data"),
        ("backend", "uploads_data", "/app/uploads"),
        (
            "ml-service",
            "huggingface_cache",
            "/root/.cache/huggingface",
        ),
        ("ml-service", "nltk_cache", "/root/nltk_data"),
    }
    actual_mounts = {
        (service_name, mount.get("source"), mount.get("target"))
        for service_name, service in services.items()
        for mount in service.get("volumes", [])
        if mount.get("type") == "volume"
    }
    for service_name, source, target in sorted(expected_mounts):
        require(
            errors,
            (service_name, source, target) in actual_mounts,
            f"{service_name}: '{source}' must mount at '{target}'",
        )

    image = database.get("image", "")
    require(
        errors,
        image.startswith("postgis/postgis:")
        and not image.endswith(":latest"),
        "db: use an explicitly tagged postgis/postgis image",
    )

    ai_provider = backend_environment.get("AI_PROVIDER")
    media_provider = backend_environment.get("MEDIA_STORAGE_PROVIDER")
    phone_otp_provider = backend_environment.get(
        "PHONE_OTP_PROVIDER"
    )
    azure_enabled = (
        backend_environment.get("AZURE_ENABLED", "").lower() == "true"
    )
    fallback_enabled = (
        backend_environment.get("AI_FALLBACK_ENABLED", "").lower()
        == "true"
    )
    video_indexer_enabled = (
        backend_environment.get(
            "AZURE_VIDEO_INDEXER_ENABLED", ""
        ).lower()
        == "true"
    )

    require(
        errors,
        ai_provider in {"local", "azure", "hybrid"},
        "backend: AI_PROVIDER must be local, azure, or hybrid",
    )
    require(
        errors,
        media_provider in {"local", "azure_blob"},
        "backend: MEDIA_STORAGE_PROVIDER must be local or azure_blob",
    )
    require(
        errors,
        phone_otp_provider in {"disabled", "acs"},
        "backend: production PHONE_OTP_PROVIDER must be disabled or acs",
    )

    if ai_provider in {"azure", "hybrid"}:
        require(
            errors,
            azure_enabled,
            "backend: AZURE_ENABLED must be true for azure or hybrid AI",
        )
        require(
            errors,
            bool(backend_environment.get("AZURE_OPENAI_ENDPOINT")),
            "backend: AZURE_OPENAI_ENDPOINT is required for azure AI",
        )
        require(
            errors,
            bool(backend_environment.get("AZURE_OPENAI_API_KEY")),
            "backend: AZURE_OPENAI_API_KEY is required for azure AI",
        )

    if ai_provider == "local" and fallback_enabled:
        require(
            errors,
            azure_enabled,
            "backend: AZURE_ENABLED must be true for local-to-azure fallback",
        )
        require(
            errors,
            bool(backend_environment.get("AZURE_OPENAI_ENDPOINT")),
            "backend: AZURE_OPENAI_ENDPOINT is required for AI fallback",
        )

    if media_provider == "azure_blob":
        require(
            errors,
            azure_enabled,
            "backend: AZURE_ENABLED must be true for Blob media storage",
        )
        require(
            errors,
            bool(
                backend_environment.get(
                    "AZURE_STORAGE_CONNECTION_STRING"
                )
            ),
            "backend: AZURE_STORAGE_CONNECTION_STRING is required for "
            "Blob media storage",
        )
        require(
            errors,
            bool(backend_environment.get("AZURE_STORAGE_CONTAINER")),
            "backend: AZURE_STORAGE_CONTAINER is required for Blob "
            "media storage",
        )

    if phone_otp_provider == "acs":
        require(
            errors,
            azure_enabled,
            "backend: AZURE_ENABLED must be true for ACS phone OTP",
        )
        require(
            errors,
            bool(backend_environment.get("ACS_CONNECTION_STRING")),
            "backend: ACS_CONNECTION_STRING is required for ACS phone OTP",
        )
        require(
            errors,
            bool(backend_environment.get("ACS_SMS_FROM")),
            "backend: ACS_SMS_FROM is required for ACS phone OTP",
        )

    if video_indexer_enabled:
        require(
            errors,
            azure_enabled,
            "backend: AZURE_ENABLED must be true for Video Indexer",
        )
        require(
            errors,
            bool(
                backend_environment.get(
                    "AZURE_VIDEO_INDEXER_ACCOUNT_ID"
                )
            ),
            "backend: AZURE_VIDEO_INDEXER_ACCOUNT_ID is required for "
            "Video Indexer",
        )
        require(
            errors,
            bool(backend_environment.get("AZURE_VIDEO_INDEXER_API_KEY")),
            "backend: AZURE_VIDEO_INDEXER_API_KEY is required for "
            "Video Indexer",
        )
        # Video Indexer downloads the video over the public internet, so
        # locally stored media at a relative /uploads path is unreachable.
        require(
            errors,
            media_provider == "azure_blob",
            "backend: AZURE_VIDEO_INDEXER_ENABLED requires "
            "MEDIA_STORAGE_PROVIDER=azure_blob",
        )

    cors_origins = backend_environment.get("CORS_ORIGINS", "")
    require(
        errors,
        "localhost" not in cors_origins
        and "127.0.0.1" not in cors_origins,
        "backend: production CORS_ORIGINS cannot contain localhost",
    )

    database_url = backend_environment.get("DATABASE_URL", "")
    require(
        errors,
        urlparse(database_url).hostname == "db",
        "backend: DATABASE_URL must use the internal 'db' hostname",
    )

    if not allow_example_secrets:
        secret_key = backend_environment.get("SECRET_KEY", "")
        database_password = (
            database.get("environment", {}).get("POSTGRES_PASSWORD", "")
        )
        require(
            errors,
            has_strong_secret(secret_key, 32),
            "backend: SECRET_KEY must be at least 32 non-placeholder characters",
        )
        require(
            errors,
            has_strong_secret(database_password, 16),
            "db: POSTGRES_PASSWORD must be at least 16 non-placeholder characters",
        )
        require(
            errors,
            not any(
                marker in database_url.lower()
                for marker in PLACEHOLDER_MARKERS
            ),
            "backend: DATABASE_URL cannot contain a placeholder",
        )

        # Azure credentials are copied by hand out of the portal, so a
        # half-finished .env.production is the likely failure. Only check
        # the keys that are actually in use; a blank value is already
        # caught by the per-provider requirements above.
        if azure_enabled:
            for variable in AZURE_SECRET_VARIABLES:
                value = backend_environment.get(variable, "")
                require(
                    errors,
                    not any(
                        marker in value.lower()
                        for marker in PLACEHOLDER_MARKERS
                    ),
                    f"backend: {variable} cannot contain a placeholder",
                )

    return errors


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Tat-Sahayk production Compose configuration."
    )
    parser.add_argument(
        "--compose-file",
        default="docker-compose.production.yml",
    )
    parser.add_argument("--env-file", default=".env.production")
    parser.add_argument(
        "--allow-example-secrets",
        action="store_true",
        help="Allow documented placeholder secrets during CI validation",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    repository_root = Path(__file__).resolve().parents[1]
    compose_file = (repository_root / arguments.compose_file).resolve()
    env_file = (repository_root / arguments.env_file).resolve()

    for label, path in (
        ("Compose file", compose_file),
        ("Environment file", env_file),
    ):
        if not path.is_file():
            print(f"{label} not found: {path}", file=sys.stderr)
            return 2

    try:
        config = load_rendered_config(
            repository_root,
            compose_file,
            env_file,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    errors = validate_config(
        config,
        allow_example_secrets=arguments.allow_example_secrets,
    )
    if errors:
        print("Production Compose validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        "Production Compose validation passed: 4 services, "
        "frontend-only port exposure, named persistence, "
        "and production-safe settings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
