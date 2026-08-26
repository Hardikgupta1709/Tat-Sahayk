"""Lazily constructed Azure SDK clients.

Every Azure SDK import happens inside a factory rather than at module
scope, so a deployment that leaves ``AZURE_ENABLED=false`` never imports
or initializes an Azure client. Each factory is cached because the SDK
clients are reusable and hold connection pools.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings


logger = logging.getLogger(__name__)


class AzureServiceError(RuntimeError):
    """Raised when an Azure integration is unavailable."""


def _require_enabled(integration: str) -> None:
    if not settings.AZURE_ENABLED:
        raise AzureServiceError(
            f"{integration} is unavailable because "
            "AZURE_ENABLED is false"
        )


@lru_cache(maxsize=1)
def get_openai_client() -> Any:
    """Return a cached Azure OpenAI client."""
    _require_enabled("Azure OpenAI")

    if not settings.azure_openai_configured:
        raise AzureServiceError(
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY "
            "are required for Azure OpenAI"
        )

    try:
        from openai import AzureOpenAI
    except ImportError as exc:  # pragma: no cover
        raise AzureServiceError(
            "The openai package is not installed"
        ) from exc

    try:
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    except Exception as exc:
        raise AzureServiceError(
            "Unable to initialize the Azure OpenAI client"
        ) from exc


@lru_cache(maxsize=1)
def get_blob_service_client() -> Any:
    """Return a cached Azure Blob Storage service client."""
    _require_enabled("Azure Blob Storage")

    if not settings.AZURE_STORAGE_CONNECTION_STRING:
        raise AzureServiceError(
            "AZURE_STORAGE_CONNECTION_STRING is required for "
            "Azure Blob Storage"
        )

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:  # pragma: no cover
        raise AzureServiceError(
            "The azure-storage-blob package is not installed"
        ) from exc

    try:
        return BlobServiceClient.from_connection_string(
            settings.AZURE_STORAGE_CONNECTION_STRING
        )
    except Exception as exc:
        raise AzureServiceError(
            "Unable to initialize the Azure Blob Storage client"
        ) from exc


@lru_cache(maxsize=1)
def get_email_client() -> Any:
    """Return a cached Azure Communication Services email client."""
    _require_enabled("Azure Communication Services email")

    if not settings.acs_email_configured:
        raise AzureServiceError(
            "ACS_CONNECTION_STRING and ACS_SENDER_EMAIL are "
            "required for Azure Communication Services email"
        )

    try:
        from azure.communication.email import EmailClient
    except ImportError as exc:  # pragma: no cover
        raise AzureServiceError(
            "The azure-communication-email package is not "
            "installed"
        ) from exc

    try:
        return EmailClient.from_connection_string(
            settings.ACS_CONNECTION_STRING
        )
    except Exception as exc:
        raise AzureServiceError(
            "Unable to initialize the Azure Communication "
            "Services email client"
        ) from exc


@lru_cache(maxsize=1)
def get_sms_client() -> Any:
    """Return a cached Azure Communication Services SMS client."""
    _require_enabled("Azure Communication Services SMS")

    if not settings.acs_configured:
        raise AzureServiceError(
            "ACS_CONNECTION_STRING is required for Azure "
            "Communication Services SMS"
        )

    try:
        from azure.communication.sms import SmsClient
    except ImportError as exc:  # pragma: no cover
        raise AzureServiceError(
            "The azure-communication-sms package is not "
            "installed"
        ) from exc

    try:
        return SmsClient.from_connection_string(
            settings.ACS_CONNECTION_STRING
        )
    except Exception as exc:
        raise AzureServiceError(
            "Unable to initialize the Azure Communication "
            "Services SMS client"
        ) from exc


def blob_account_host() -> str | None:
    """Return the configured blob account hostname, when known."""
    if settings.AZURE_STORAGE_ACCOUNT:
        return (
            f"{settings.AZURE_STORAGE_ACCOUNT}"
            ".blob.core.windows.net"
        ).lower()

    return None


def parse_blob_url(url: str) -> tuple[str, str] | None:
    """Split a blob URL into ``(container, blob_name)``.

    Returns ``None`` when the URL does not point at the configured
    storage account, so callers can fall back to a plain HTTP fetch.
    """
    if not url:
        return None

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").lower()

    if not host.endswith(".blob.core.windows.net"):
        return None

    expected_host = blob_account_host()

    if expected_host and host != expected_host:
        return None

    path = parsed.path.lstrip("/")
    container, separator, blob_name = path.partition("/")

    if not separator or not container or not blob_name:
        return None

    return container, blob_name
