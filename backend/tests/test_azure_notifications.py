"""Tests for the batched Azure Communication Services alert fan-out.

Batching exists because an Azure-managed sender domain allows only 10
emails per hour, so these tests pin the properties that keep an alert
inside that budget: one request per chunk, bcc-only recipients, a hard
message cap with a logged skip count, and a single retry on throttling.
"""

import logging

import pytest

from app.services import azure_notifications


class FakePoller:
    def __init__(self, status: str) -> None:
        self._status = status

    def result(self) -> dict:
        return {"status": self._status}


class FakeEmailClient:
    def __init__(self, status: str = "Succeeded") -> None:
        self.status = status
        self.messages = []

    def begin_send(self, message):
        self.messages.append(message)
        return FakePoller(self.status)


class ThrottledOnce(FakeEmailClient):
    """Raises a 429 on the first send, then succeeds."""

    def __init__(self, retry_after: str = "3") -> None:
        super().__init__()
        self.retry_after = retry_after
        self.raised = False

    def begin_send(self, message):
        if not self.raised:
            self.raised = True
            raise FakeThrottleError(self.retry_after)

        return super().begin_send(message)


class FakeResponse:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


class FakeThrottleError(Exception):
    def __init__(self, retry_after: str) -> None:
        super().__init__("Too many requests")
        self.status_code = 429
        self.response = FakeResponse({"Retry-After": retry_after})


@pytest.fixture
def acs_enabled(monkeypatch):
    monkeypatch.setattr(
        azure_notifications.settings,
        "AZURE_ENABLED",
        True,
    )
    monkeypatch.setattr(
        azure_notifications.settings,
        "ACS_CONNECTION_STRING",
        "endpoint=https://example.communication.azure.com/;accesskey=k",
    )
    monkeypatch.setattr(
        azure_notifications.settings,
        "ACS_SENDER_EMAIL",
        "DoNotReply@alerts.invalid",
    )
    monkeypatch.setattr(
        azure_notifications.settings,
        "ACS_EMAIL_RECIPIENTS_PER_MESSAGE",
        50,
    )
    monkeypatch.setattr(
        azure_notifications.settings,
        "ACS_EMAIL_MAX_MESSAGES_PER_ALERT",
        2,
    )


def install_client(monkeypatch, client):
    monkeypatch.setattr(
        azure_notifications,
        "get_email_client",
        lambda: client,
    )

    return client


def send(recipients):
    return azure_notifications.send_disaster_alert_emails(
        recipients,
        disaster_type="flood",
        location="12.9716°N, 77.5946°E",
        severity="high",
        description="Water entered the coastal road.",
    )


def addresses(count: int, offset: int = 0) -> list[str]:
    return [
        f"citizen{index}@example.invalid"
        for index in range(offset, offset + count)
    ]


def test_recipients_are_chunked_at_the_configured_size(
    acs_enabled,
    monkeypatch,
):
    client = install_client(monkeypatch, FakeEmailClient())

    delivered = send(addresses(51))

    assert delivered == 51
    assert len(client.messages) == 2
    assert len(
        client.messages[0]["recipients"]["bcc"]
    ) == 50
    assert len(
        client.messages[1]["recipients"]["bcc"]
    ) == 1


def test_recipients_are_bcc_only(
    acs_enabled,
    monkeypatch,
):
    client = install_client(monkeypatch, FakeEmailClient())

    send(addresses(3))

    recipients = client.messages[0]["recipients"]

    # Batching citizens into "to" would disclose every address to
    # everyone else on the alert.
    assert set(recipients) == {"bcc"}
    assert [entry["address"] for entry in recipients["bcc"]] == (
        addresses(3)
    )
    assert client.messages[0]["senderAddress"] == (
        "DoNotReply@alerts.invalid"
    )


def test_duplicate_addresses_are_sent_once(
    acs_enabled,
    monkeypatch,
):
    client = install_client(monkeypatch, FakeEmailClient())

    delivered = send(
        [
            "one@example.invalid",
            " one@example.invalid ",
            "ONE@example.invalid",
            "",
            "two@example.invalid",
        ]
    )

    assert delivered == 2
    assert [
        entry["address"]
        for entry in client.messages[0]["recipients"]["bcc"]
    ] == ["one@example.invalid", "two@example.invalid"]


def test_message_cap_skips_the_remainder_and_logs_it(
    acs_enabled,
    monkeypatch,
    caplog,
):
    client = install_client(monkeypatch, FakeEmailClient())

    with caplog.at_level(
        logging.WARNING,
        logger=azure_notifications.logger.name,
    ):
        delivered = send(addresses(120))

    assert delivered == 100
    assert len(client.messages) == 2

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]

    assert any(
        "20 of 120" in message for message in warnings
    ), warnings


def test_throttling_is_retried_once(
    acs_enabled,
    monkeypatch,
):
    client = install_client(monkeypatch, ThrottledOnce())
    slept = []
    monkeypatch.setattr(
        azure_notifications.time,
        "sleep",
        slept.append,
    )

    delivered = send(addresses(2))

    assert delivered == 2
    assert slept == [3.0]
    assert len(client.messages) == 1


def test_retry_after_is_capped(
    acs_enabled,
    monkeypatch,
):
    client = install_client(
        monkeypatch,
        ThrottledOnce(retry_after="9000"),
    )
    slept = []
    monkeypatch.setattr(
        azure_notifications.time,
        "sleep",
        slept.append,
    )

    assert send(addresses(1)) == 1
    assert slept == [
        float(azure_notifications.MAX_RETRY_AFTER_SECONDS)
    ]
    assert len(client.messages) == 1


def test_non_throttling_failure_is_not_retried(
    acs_enabled,
    monkeypatch,
):
    calls = []

    class BrokenClient:
        def begin_send(self, message):
            calls.append(message)
            raise RuntimeError("sender domain not verified")

    install_client(monkeypatch, BrokenClient())

    assert send(addresses(2)) == 0
    assert len(calls) == 1


def test_unsuccessful_status_is_not_counted(
    acs_enabled,
    monkeypatch,
):
    install_client(
        monkeypatch,
        FakeEmailClient(status="Failed"),
    )

    assert send(addresses(2)) == 0


def test_nothing_is_sent_when_acs_is_unconfigured(
    monkeypatch,
):
    monkeypatch.setattr(
        azure_notifications.settings,
        "AZURE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        azure_notifications,
        "get_email_client",
        lambda: pytest.fail("ACS client should not be created"),
    )

    assert send(addresses(3)) == 0


def test_empty_recipient_list_is_a_no_op(
    acs_enabled,
    monkeypatch,
):
    monkeypatch.setattr(
        azure_notifications,
        "get_email_client",
        lambda: pytest.fail("ACS client should not be created"),
    )

    assert send([]) == 0
    assert send(["", "   "]) == 0
