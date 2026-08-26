import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings


CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class MediaStorageError(RuntimeError):
    """Raised when media cannot be persisted."""


class MediaStorage(Protocol):
    def save(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        """Persist media and return its public URL."""


def build_object_name(content_type: str) -> str:
    extension = CONTENT_TYPE_EXTENSIONS.get(
        content_type.lower(),
        ".bin",
    )

    return f"{uuid.uuid4().hex}{extension}"


class LocalMediaStorage:
    def __init__(
        self,
        directory: str | Path,
        public_url: str,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        normalized_url = public_url.strip().rstrip("/")
        self.public_url = normalized_url or "/uploads"

    def save(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        del filename

        object_name = build_object_name(content_type)
        destination = self.directory / object_name

        try:
            destination.write_bytes(file_bytes)
        except OSError as exc:
            raise MediaStorageError(
                "Unable to write media to local storage"
            ) from exc

        return f"{self.public_url}/{object_name}"


class _FallbackContentSettings:
    """Stand-in for the SDK's ``ContentSettings``.

    Only reachable with an injected client, since a real Blob client
    cannot exist without the SDK installed. Keeps the content type on
    the upload call either way.
    """

    def __init__(self, content_type: str) -> None:
        self.content_type = content_type


def _content_settings(content_type: str) -> Any:
    try:
        from azure.storage.blob import ContentSettings
    except ImportError:
        return _FallbackContentSettings(content_type)

    return ContentSettings(content_type=content_type)


class AzureBlobMediaStorage:
    def __init__(
        self,
        container: str,
        connection_string: str | None = None,
        account: str | None = None,
        client: Any = None,
    ) -> None:
        if not container:
            raise MediaStorageError(
                "AZURE_STORAGE_CONTAINER is required for "
                "Azure Blob media storage"
            )

        self.container = container
        self.account = account

        if client is not None:
            self.client = client
            return

        if not connection_string:
            raise MediaStorageError(
                "AZURE_STORAGE_CONNECTION_STRING is required "
                "for Azure Blob media storage"
            )

        try:
            # Imported lazily so local storage never loads the
            # Azure SDK.
            from azure.storage.blob import BlobServiceClient

            self.client = (
                BlobServiceClient.from_connection_string(
                    connection_string
                )
            )
        except Exception as exc:
            raise MediaStorageError(
                "Unable to initialize Azure Blob media storage"
            ) from exc

    def save(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        del filename

        object_name = build_object_name(content_type)
        blob_name = f"reports/{object_name}"

        try:
            blob_client = self.client.get_blob_client(
                container=self.container,
                blob=blob_name,
            )
            blob_client.upload_blob(
                file_bytes,
                overwrite=False,
                content_settings=_content_settings(content_type),
            )
        except Exception as exc:
            raise MediaStorageError(
                "Unable to upload media to Azure Blob Storage"
            ) from exc

        url = getattr(blob_client, "url", None)

        if url:
            return url

        return (
            f"https://{self.account}.blob.core.windows.net/"
            f"{self.container}/{blob_name}"
        )


@lru_cache
def get_media_storage() -> MediaStorage:
    if settings.MEDIA_STORAGE_PROVIDER == "local":
        return LocalMediaStorage(
            directory=settings.local_media_directory,
            public_url=settings.local_media_url,
        )

    return AzureBlobMediaStorage(
        container=settings.AZURE_STORAGE_CONTAINER,
        connection_string=(
            settings.AZURE_STORAGE_CONNECTION_STRING
        ),
        account=settings.AZURE_STORAGE_ACCOUNT,
    )
