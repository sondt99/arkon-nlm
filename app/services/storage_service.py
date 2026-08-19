"""
MinIO storage service — file upload, download, presigned URLs.
"""

import asyncio
import io
from datetime import timedelta
from typing import IO, Optional

from loguru import logger
from minio import Minio
from minio.error import S3Error

from app.config import settings


def safe_relative_path(path: str) -> str:
    """Normalize a user-supplied relative object path and reject traversal.

    Raises ValueError on absolute paths, backslashes resolving to traversal,
    or any '..' segment — callers must translate that into an HTTP 400.
    """
    normalized = (path or "").replace("\\", "/").lstrip("/")
    parts = [seg for seg in normalized.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise ValueError(f"Unsafe object path: {path!r}")
    return "/".join(parts)


class StorageService:
    """S3-compatible object storage via MinIO.

    Every method here is synchronous: the MinIO SDK is urllib3, so each call is a blocking
    socket round-trip. Each one therefore has an `*_async` twin that offloads to a worker
    thread — coroutines MUST use those. Calling the sync form from a coroutine holds the
    event loop for the whole transfer, which is how one 300 MB upload used to stall every
    other request in the process, `/health` included.
    """

    def __init__(self):
        self._client: Optional[Minio] = None
        self._presign_client: Optional[Minio] = None

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
        return self._client

    @property
    def presign_client(self) -> Minio:
        """Separate client using the public endpoint so presigned URL signatures match.

        Pre-seeds the bucket region to avoid a connectivity check against the public
        endpoint (which may be unreachable from inside the Docker container).
        MinIO always uses us-east-1 by default.
        """
        if self._presign_client is None:
            public = settings.minio_public_endpoint or settings.minio_endpoint
            client = Minio(
                endpoint=public,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            client._region_map[settings.minio_bucket] = "us-east-1"
            self._presign_client = client
        return self._presign_client

    def ensure_bucket_sync(self):
        """Create the default bucket if it doesn't exist."""
        bucket = settings.minio_bucket
        try:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                logger.info(f"Created MinIO bucket: {bucket}")
            else:
                logger.debug(f"MinIO bucket already exists: {bucket}")
        except S3Error as e:
            logger.error(f"Failed to ensure MinIO bucket: {e}")
            raise

    async def ensure_bucket(self):
        """Non-blocking wrapper for ensure_bucket_sync using asyncio.to_thread.

        This was the most misleading site in the file: an `async def` whose body was two
        bare urllib3 round-trips, so `await ensure_bucket()` looked like it yielded and
        never did. It runs on the API's startup path, where a slow or unreachable MinIO
        froze the loop before the server could answer anything at all.
        """
        await asyncio.to_thread(self.ensure_bucket_sync)

    def bucket_exists_sync(self) -> bool:
        """Read-only probe: does the configured bucket exist? Raises if MinIO is down.

        Deliberately not `ensure_bucket`: a health probe must not *create* anything.
        `/health` used to call `ensure_bucket()`, so the container's 15-second liveness
        check silently made the bucket — which masked a misconfigured MINIO_BUCKET and
        turned a read-only check into a write against object storage.
        """
        return self.client.bucket_exists(settings.minio_bucket)

    async def bucket_exists(self) -> bool:
        """Non-blocking wrapper for bucket_exists_sync using asyncio.to_thread."""
        return await asyncio.to_thread(self.bucket_exists_sync)

    def upload_file(
        self,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file to MinIO. Returns the object key."""
        bucket = settings.minio_bucket
        self.client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        logger.debug(f"Uploaded {object_name} to MinIO ({len(data)} bytes)")
        return object_name

    async def upload_file_async(
        self,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Non-blocking wrapper for upload_file using asyncio.to_thread."""
        return await asyncio.to_thread(self.upload_file, object_name, data, content_type)

    def download_file(self, object_name: str) -> bytes:
        """Download a file from MinIO and return its bytes."""
        bucket = settings.minio_bucket
        response = None
        try:
            response = self.client.get_object(bucket, object_name)
            return response.read()
        finally:
            if response:
                response.close()
                response.release_conn()

    async def download_file_async(self, object_name: str) -> bytes:
        """Non-blocking wrapper for download_file using asyncio.to_thread.

        The offload has to cover `response.read()` as well as `get_object`, which is why
        the whole sync method is handed to the thread rather than just the call that
        returns the response — get_object only sends the request headers; the body is
        streamed by read().
        """
        return await asyncio.to_thread(self.download_file, object_name)

    def upload_stream(
        self,
        object_name: str,
        stream: IO[bytes],
        length: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload from a stream."""
        bucket = settings.minio_bucket
        self.client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=stream,  # type: ignore[arg-type]
            length=length,
            content_type=content_type,
        )
        return object_name

    async def upload_stream_async(
        self,
        object_name: str,
        stream: IO[bytes],
        length: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Non-blocking wrapper for upload_stream using asyncio.to_thread."""
        return await asyncio.to_thread(
            self.upload_stream, object_name, stream, length, content_type
        )

    def get_presigned_url(
        self,
        object_name: str,
        expiry_hours: Optional[int] = None,
    ) -> str:
        """Generate a presigned download URL using the public-facing endpoint.

        Uses a dedicated client configured with minio_public_endpoint so the
        HMAC signature is computed against the browser-accessible hostname.
        """
        # After issuance a presigned URL is an unauthenticated bearer capability: it
        # survives permission revocation and account deletion, so its lifetime is the real
        # exposure window. The default is now 30 minutes rather than 24 hours.
        if expiry_hours is not None:
            expires = timedelta(hours=expiry_hours)
        else:
            expires = timedelta(minutes=settings.minio_presign_expiry_minutes)
        return self.presign_client.presigned_get_object(
            bucket_name=settings.minio_bucket,
            object_name=object_name,
            expires=expires,
        )

    async def get_presigned_url_async(
        self,
        object_name: str,
        expiry_hours: Optional[int] = None,
    ) -> str:
        """Non-blocking wrapper for get_presigned_url using asyncio.to_thread.

        Signing itself is local HMAC, but presigned_get_object resolves the bucket region
        first and only skips the network for the one bucket presign_client pre-seeds — any
        other bucket, or a region-map miss, turns this into a blocking GetBucketLocation.
        """
        return await asyncio.to_thread(self.get_presigned_url, object_name, expiry_hours)

    def delete_object(self, object_name: str):
        """Delete a file from MinIO."""
        self.client.remove_object(settings.minio_bucket, object_name)
        logger.debug(f"Deleted {object_name} from MinIO")

    async def delete_object_async(self, object_name: str):
        """Non-blocking wrapper for delete_object using asyncio.to_thread."""
        await asyncio.to_thread(self.delete_object, object_name)

    def list_objects(self, prefix: str, recursive: bool = True):
        """List all objects under a given prefix."""
        return self.client.list_objects(settings.minio_bucket, prefix=prefix, recursive=recursive)

    async def list_objects_async(self, prefix: str, recursive: bool = True) -> list:
        """Non-blocking wrapper for list_objects using asyncio.to_thread.

        Returns a materialised list, not a generator. minio's list_objects is lazy: the
        paginated ListObjects requests are issued while the generator is *drained*, so
        offloading only the call that returns it would move nothing off the loop and leave
        every `for obj in ...` step blocking. The drain has to happen inside the thread.
        """
        return await asyncio.to_thread(lambda: list(self.list_objects(prefix, recursive)))

    def delete_prefix(self, prefix: str):
        """Delete all objects with a given prefix (e.g. a source's files)."""
        objects = self.client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True)
        for obj in objects:
            if obj.object_name:
                self.client.remove_object(settings.minio_bucket, obj.object_name)
        logger.debug(f"Deleted all objects with prefix: {prefix}")

    async def delete_prefix_async(self, prefix: str):
        """Non-blocking wrapper for delete_prefix using asyncio.to_thread.

        One offload covers the whole loop — the list drain plus one remove_object per
        object. Deleting a source with hundreds of extracted images is that many
        sequential round-trips.
        """
        await asyncio.to_thread(self.delete_prefix, prefix)

    def copy_object(self, src_key: str, dest_key: str):
        """Copy a single object within the same bucket."""
        from minio.commonconfig import CopySource
        bucket = settings.minio_bucket
        self.client.copy_object(
            bucket,
            dest_key,
            CopySource(bucket, src_key),
        )

    def _is_single_object(self, prefix: str) -> bool:
        """True if `prefix` names one object, False if it is a folder prefix.

        Only a genuine missing-key error means "not a single object". Everything else —
        a transient 503, an auth failure, a network blip — is re-raised.

        The previous form was `except Exception: is_file = False`, evaluated independently
        in copy_prefix and then again in move_prefix. A transient error made a file look
        like a folder, and because the two calls could disagree, move_prefix could delete
        a file that copy_prefix had just failed to copy. See move_prefix.
        """
        try:
            self.client.stat_object(settings.minio_bucket, prefix)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return False
            raise

    def copy_prefix(self, src_prefix: str, dest_prefix: str, is_file: Optional[bool] = None):
        """Copy all objects from one prefix to another (recursively).

        `is_file` may be passed by a caller that already determined it, so the two code
        paths cannot reach different conclusions about the same prefix.
        """
        bucket = settings.minio_bucket

        if is_file is None:
            is_file = self._is_single_object(src_prefix)

        if is_file:
            # Single file copy - do NOT add slashes
            self.copy_object(src_prefix, dest_prefix)
            logger.debug(f"Copied file {src_prefix} to {dest_prefix}")
            return

        # Directory copy - MUST ensure trailing slashes to avoid partial matches
        src_p = src_prefix if src_prefix.endswith("/") else f"{src_prefix}/"
        dest_p = dest_prefix if dest_prefix.endswith("/") else f"{dest_prefix}/"
        
        # List all objects under the folder prefix
        objects = self.client.list_objects(bucket, prefix=src_p, recursive=True)
        count = 0
        for obj in objects:
            rel_path = obj.object_name.replace(src_p, "", 1)
            dest_key = f"{dest_p}{rel_path}"
            self.copy_object(obj.object_name, dest_key)
            count += 1
        
        logger.info(f"Copied folder content ({count} objects) from {src_p} to {dest_p}")

    async def copy_prefix_async(
        self, src_prefix: str, dest_prefix: str, is_file: Optional[bool] = None
    ):
        """Non-blocking wrapper for copy_prefix using asyncio.to_thread."""
        await asyncio.to_thread(self.copy_prefix, src_prefix, dest_prefix, is_file)

    def move_prefix(self, src_prefix: str, dest_prefix: str):
        """Move all objects from one prefix to another (recursively), then delete source.

        Data-loss path this guards against: is_file used to be computed twice, once here
        and once inside copy_prefix, each swallowing every exception. A transient MinIO
        error on the second call made copy_prefix treat a file as a folder — it listed
        `<file>/`, found zero objects, logged "Copied folder content (0 objects)" at INFO,
        and returned. Control came back here still believing it was a file, so
        delete_object ran. The file was destroyed, nothing was copied, and the operation
        reported success.

        Now the determination is made once and passed down.
        """
        is_file = self._is_single_object(src_prefix)

        self.copy_prefix(src_prefix, dest_prefix, is_file=is_file)

        if is_file:
            self.delete_object(src_prefix)
        else:
            # Delete folder with trailing slash to be safe
            src_p = src_prefix if src_prefix.endswith("/") else f"{src_prefix}/"
            self.delete_prefix(src_p)
            
        logger.info(f"Moved {src_prefix} to {dest_prefix}")

    async def move_prefix_async(self, src_prefix: str, dest_prefix: str):
        """Non-blocking wrapper for move_prefix using asyncio.to_thread.

        The stat/copy/delete sequence must stay in one offload: splitting it would let the
        loop interleave another mover between the copy and the delete, and move_prefix's
        whole point is that the is_file determination is made once for both halves.
        """
        await asyncio.to_thread(self.move_prefix, src_prefix, dest_prefix)

    def calculate_prefix_hash(self, prefix: str) -> str:
        """
        Calculate a unique hash for all objects under a prefix.
        Uses object names and ETags to detect any content or structure change.
        """
        import hashlib
        bucket = settings.minio_bucket
        p = prefix if prefix.endswith("/") else f"{prefix}/"
        
        objects = self.client.list_objects(bucket, prefix=p, recursive=True)
        # Sort objects by name to ensure stable hash
        sorted_objects = sorted(objects, key=lambda x: x.object_name)
        
        hasher = hashlib.sha256()
        for obj in sorted_objects:
            rel_path = obj.object_name.replace(p, "", 1)
            # Combine path and etag
            hasher.update(rel_path.encode("utf-8"))
            hasher.update(obj.etag.encode("utf-8"))

        return hasher.hexdigest()

    async def calculate_prefix_hash_async(self, prefix: str) -> str:
        """Non-blocking wrapper for calculate_prefix_hash using asyncio.to_thread.

        `sorted(objects)` is the drain of a lazy minio generator, so the paginated listing
        happens here rather than on the line above it.
        """
        return await asyncio.to_thread(self.calculate_prefix_hash, prefix)


# Singleton
storage_service = StorageService()
