from pathlib import Path

import pytest

from app.services.media_storage import (
    AzureBlobMediaStorage,
    LocalMediaStorage,
    MediaStorageError,
)


class FakeBlobClient:
    def __init__(self, account: str, container: str, blob: str) -> None:
        self.container = container
        self.blob = blob
        self.url = (
            f"https://{account}.blob.core.windows.net/"
            f"{container}/{blob}"
        )
        self.upload_calls = []

    def upload_blob(self, data, **kwargs) -> None:
        self.upload_calls.append({"data": data, **kwargs})


class FakeBlobServiceClient:
    def __init__(self, account: str = "tatsahayktest") -> None:
        self.account = account
        self.blob_clients = []

    def get_blob_client(self, container: str, blob: str):
        client = FakeBlobClient(self.account, container, blob)
        self.blob_clients.append(client)
        return client


def test_local_storage_writes_media(
    tmp_path: Path,
):
    storage = LocalMediaStorage(
        directory=tmp_path,
        public_url="/uploads",
    )

    public_url = storage.save(
        file_bytes=b"prototype-image",
        filename="evidence.png",
        content_type="image/png",
    )

    assert public_url.startswith("/uploads/")
    assert public_url.endswith(".png")

    object_name = public_url.removeprefix(
        "/uploads/"
    )

    assert (
        tmp_path / object_name
    ).read_bytes() == b"prototype-image"


def test_local_storage_generates_unique_names(
    tmp_path: Path,
):
    storage = LocalMediaStorage(
        directory=tmp_path,
        public_url="/uploads",
    )

    first_url = storage.save(
        b"first",
        "same-name.jpg",
        "image/jpeg",
    )
    second_url = storage.save(
        b"second",
        "same-name.jpg",
        "image/jpeg",
    )

    assert first_url != second_url


def test_blob_storage_uploads_with_content_type():
    client = FakeBlobServiceClient(account="tatsahayktest")

    storage = AzureBlobMediaStorage(
        container="report-media",
        account="tatsahayktest",
        client=client,
    )

    public_url = storage.save(
        file_bytes=b"image-data",
        filename="evidence.webp",
        content_type="image/webp",
    )

    assert public_url.startswith(
        "https://tatsahayktest.blob.core.windows.net/"
        "report-media/reports/"
    )
    assert public_url.endswith(".webp")

    assert len(client.blob_clients) == 1

    blob_client = client.blob_clients[0]

    assert blob_client.container == "report-media"
    assert blob_client.blob.startswith("reports/")
    assert len(blob_client.upload_calls) == 1

    call = blob_client.upload_calls[0]

    assert call["data"] == b"image-data"
    # An existing blob must never be replaced: object names are random,
    # so a collision means something is wrong rather than a re-upload.
    assert call["overwrite"] is False
    assert (
        call["content_settings"].content_type == "image/webp"
    )


def test_blob_storage_requires_credentials_without_a_client():
    with pytest.raises(MediaStorageError):
        AzureBlobMediaStorage(container="report-media")
