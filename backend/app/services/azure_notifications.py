"""Azure Communication Services SMS and email delivery.

Email alerts are batched rather than sent one per recipient. Azure
Communication Services accepts at most 50 recipients per message, and an
Azure-managed ``*.azurecomm.net`` domain is capped at 5 messages per
minute and 10 per hour with no way to raise the limit, so a per-user loop
would exhaust the hourly budget on a single alert.

Batched recipients always go in ``bcc``. Putting them in ``to`` would
disclose every recipient's address to all the others.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Sequence

from app.core.config import settings
from app.services.azure_clients import (
    AzureServiceError,
    get_email_client,
    get_sms_client,
)


logger = logging.getLogger(__name__)

# A 429 from Azure Communication Services carries Retry-After. Honour it,
# but never block a background task for longer than this.
MAX_RETRY_AFTER_SECONDS = 60
SEND_ATTEMPTS = 2


def send_otp_sms(phone: str, otp: str) -> bool:
    """Send a transactional OTP through Azure Communication Services.

    Azure Communication Services does not offer phone numbers for India,
    so this path is unreachable for Indian numbers. It works with a
    number provisioned in a supported country.
    """
    if not settings.acs_configured:
        logger.warning(
            "Skipping ACS OTP because Azure Communication "
            "Services is not configured"
        )
        return False

    if not settings.ACS_SMS_FROM:
        logger.warning(
            "Skipping ACS OTP because ACS_SMS_FROM is not set"
        )
        return False

    try:
        results = get_sms_client().send(
            from_=settings.ACS_SMS_FROM,
            to=[phone],
            message=(
                "Your Tat-Sahayk verification code is: "
                f"{otp}\n\n"
                "This code expires soon. Do not share it."
            ),
            enable_delivery_report=False,
        )
    except AzureServiceError:
        logger.exception(
            "ACS OTP delivery failed to initialize"
        )
        return False
    except Exception:
        logger.exception("ACS OTP delivery failed")
        return False

    for result in results or []:
        if getattr(result, "successful", False):
            return True

    logger.warning("ACS rejected the OTP message")
    return False


def _normalize_recipients(
    recipients: Iterable[str],
) -> list[str]:
    """Return unique, non-empty addresses in their original order."""
    seen: set[str] = set()
    ordered: list[str] = []

    for recipient in recipients:
        address = (recipient or "").strip()

        if not address:
            continue

        key = address.lower()

        if key in seen:
            continue

        seen.add(key)
        ordered.append(address)

    return ordered


def _chunk(
    addresses: Sequence[str],
    size: int,
) -> list[list[str]]:
    return [
        list(addresses[index:index + size])
        for index in range(0, len(addresses), size)
    ]


def _retry_after_seconds(error: Any) -> float | None:
    """Extract a usable Retry-After delay from an Azure error."""
    if getattr(error, "status_code", None) != 429:
        return None

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}

    try:
        delay = float(headers.get("Retry-After", 0))
    except (TypeError, ValueError):
        delay = 0.0

    if delay <= 0:
        delay = 5.0

    return min(delay, MAX_RETRY_AFTER_SECONDS)


def _build_alert_content(
    disaster_type: str,
    location: str,
    severity: str,
    description: str,
) -> dict[str, str]:
    """Build the alert body.

    Batching means one body is shared by every recipient, so the message
    cannot greet individual users by name.
    """
    subject = (
        f"{severity.upper()} alert: {disaster_type} "
        "in your area"
    )
    text_body = (
        "A verified coastal-hazard report was registered "
        "near your location.\n\n"
        f"Type: {disaster_type}\n"
        f"Severity: {severity.upper()}\n"
        f"Location: {location}\n"
        f"Details: {description}\n\n"
        "Follow official guidance and local emergency services."
    )
    html_body = (
        "<html><body>"
        "<p>A verified coastal-hazard report was registered "
        "near your location.</p>"
        "<ul>"
        f"<li><strong>Type:</strong> {disaster_type}</li>"
        f"<li><strong>Severity:</strong> {severity.upper()}</li>"
        f"<li><strong>Location:</strong> {location}</li>"
        f"<li><strong>Details:</strong> {description}</li>"
        "</ul>"
        "<p>Follow official guidance and local emergency "
        "services.</p>"
        "</body></html>"
    )

    return {
        "subject": subject,
        "plainText": text_body,
        "html": html_body,
    }


def _send_one_message(
    client: Any,
    content: dict[str, str],
    chunk: Sequence[str],
) -> bool:
    message = {
        "senderAddress": settings.ACS_SENDER_EMAIL,
        "recipients": {
            "bcc": [
                {"address": address} for address in chunk
            ],
        },
        "content": content,
    }

    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            poller = client.begin_send(message)
            result = poller.result()
        except Exception as exc:
            delay = _retry_after_seconds(exc)

            if delay is not None and attempt < SEND_ATTEMPTS:
                logger.warning(
                    "ACS throttled the alert message; retrying "
                    "in %.0fs",
                    delay,
                )
                time.sleep(delay)
                continue

            logger.exception("ACS alert delivery failed")
            return False

        status = _extract_status(result)

        if status.lower() == "succeeded":
            return True

        logger.warning(
            "ACS alert finished with status %s",
            status,
        )
        return False

    return False


def _extract_status(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("status", "unknown"))

    return str(getattr(result, "status", "unknown"))


def send_disaster_alert_emails(
    recipients: Iterable[str],
    disaster_type: str,
    location: str,
    severity: str,
    description: str,
) -> int:
    """Send one batched alert per recipient chunk.

    Returns the number of addresses covered by a successfully sent
    message. Addresses beyond the configured message cap are skipped and
    logged rather than dropped silently.
    """
    addresses = _normalize_recipients(recipients)

    if not addresses:
        return 0

    if not settings.acs_email_configured:
        logger.info(
            "Skipping %d alert emails because Azure "
            "Communication Services email is not configured",
            len(addresses),
        )
        return 0

    chunks = _chunk(
        addresses,
        settings.ACS_EMAIL_RECIPIENTS_PER_MESSAGE,
    )
    allowed = settings.ACS_EMAIL_MAX_MESSAGES_PER_ALERT

    if len(chunks) > allowed:
        skipped = sum(
            len(chunk) for chunk in chunks[allowed:]
        )
        logger.warning(
            "Alert fan-out capped at %d messages: %d of %d "
            "recipients were not emailed. Raise "
            "ACS_EMAIL_MAX_MESSAGES_PER_ALERT only if the "
            "sending domain's hourly quota allows it.",
            allowed,
            skipped,
            len(addresses),
        )
        chunks = chunks[:allowed]

    try:
        client = get_email_client()
    except AzureServiceError:
        logger.exception(
            "ACS alert delivery failed to initialize"
        )
        return 0

    content = _build_alert_content(
        disaster_type=disaster_type,
        location=location,
        severity=severity,
        description=description,
    )

    delivered = 0

    for chunk in chunks:
        if _send_one_message(client, content, chunk):
            delivered += len(chunk)

    return delivered
