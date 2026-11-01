"""Thin S3 wrapper for Cloudflare R2.

Deliberately minimal: the pipeline only needs to PUT an object, DELETE a batch
of keys, and check the bucket is reachable. *What* to upload and *when* lives in
publisher.py; what *has already been* uploaded lives in publish_manifest.py.

Two rules here come straight from the free-tier budget (1M Class A ops/month):

  * **No LIST.** ListObjectsV2 is a Class A operation, so there is deliberately
    no list method on this class. The local manifest is the source of truth for
    retention and the storage cap instead.
  * **One PUT per object.** boto3's default transfer config switches to
    multipart above 8 MB, and multipart costs a Class A op per part plus two
    more for create/complete — so a 90 MB episode would burn ~14 ops instead of
    1. MULTIPART_THRESHOLD is raised past R2's 5 GiB single-PUT ceiling to keep
    every upload a single billable operation.

Object keys embed PUBLIC_PATH_SECRET, so a key must never reach a log file.
Everything logged here goes through redact_key().
"""

import logging
import os
from typing import Iterable

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

logger = logging.getLogger(__name__)

# R2 accepts a single PUT up to 5 GiB; stay just under it so boto3 never
# silently switches to multipart (see the module docstring).
_SINGLE_PUT_MAX = 5 * 1024 ** 3 - 1
_TRANSFER = TransferConfig(
    multipart_threshold=_SINGLE_PUT_MAX,
    max_concurrency=1,          # one episode at a time; this box is CPU-bound
    use_threads=False,
)

CACHE_FEED = "public, max-age=300"
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"

_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".xml": "application/rss+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def content_type_for(filename: str) -> str:
    return _CONTENT_TYPES.get(os.path.splitext(filename)[1].lower(),
                              "application/octet-stream")


# Codes that mean the credentials themselves are no good: the API token was
# revoked, rotated or mistyped. Retrying cannot help, and publishing has
# silently stopped, so these are the ones worth waking someone for.
CREDENTIAL_ERROR_CODES = frozenset({
    "AccessDenied",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "ExpiredToken",
})


def error_code(exc: BaseException) -> str | None:
    """The S3 error code from a botocore ClientError, if there is one.

    Duck-typed rather than isinstance-checked so the publisher does not need
    botocore imported, and so a test double can report a code without
    constructing a real ClientError.
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return str(code)
    return None


def redact_key(key: str, secret: str) -> str:
    """Replace the secret path segment so a key is safe to log."""
    if secret and secret in key:
        return key.replace(secret, "<SECRET>")
    return key


class R2Client:
    """S3 client for one R2 bucket, counting its own Class A operations."""

    def __init__(self, endpoint: str, bucket: str, access_key_id: str,
                 secret_access_key: str, secret_segment: str = "",
                 on_class_a=None) -> None:
        if not all([endpoint, bucket, access_key_id, secret_access_key]):
            raise ValueError(
                "R2 is not configured: R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID "
                "and R2_SECRET_ACCESS_KEY must all be set"
            )
        self.bucket = bucket
        self.secret_segment = secret_segment
        # Called with the number of Class A ops just performed, so the manifest
        # can keep a monthly tally without this class knowing about the manifest.
        self._on_class_a = on_class_a or (lambda n: None)
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def set_class_a_hook(self, fn) -> None:
        """Replace the Class A counter.

        R2Publisher calls this in its constructor so every operation is seen
        by the circuit breaker as well as the manifest. The hook given at
        construction remains the default for standalone use.
        """
        self._on_class_a = fn

    def _safe(self, key: str) -> str:
        return redact_key(key, self.secret_segment)

    def head_bucket(self) -> bool:
        """Cheap reachability/credential check. Class B, effectively free.

        Catches everything, not just ClientError: a dead endpoint or a DNS
        failure raises EndpointConnectionError, which is exactly the
        "unreachable" case this call exists to detect.
        """
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except Exception as e:
            # Never let the exception text carry credentials into a log.
            logger.error("R2 bucket %s unreachable: %s", self.bucket,
                         error_code(e) or type(e).__name__)
            return False

    def put_file(self, key: str, path: str, cache_control: str) -> int:
        """Upload a local file as a single PUT. Returns its size in bytes."""
        size = os.path.getsize(path)
        self._client.upload_file(
            Filename=path,
            Bucket=self.bucket,
            Key=key,
            ExtraArgs={
                "ContentType": content_type_for(path),
                "CacheControl": cache_control,
            },
            Config=_TRANSFER,
        )
        self._on_class_a(1)
        logger.info("Uploaded %s (%.1f MB)", self._safe(key), size / 1024 ** 2)
        return size

    def put_bytes(self, key: str, body: bytes, content_type: str,
                  cache_control: str) -> int:
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=body,
            ContentType=content_type, CacheControl=cache_control,
        )
        self._on_class_a(1)
        logger.info("Uploaded %s (%d bytes)", self._safe(key), len(body))
        return len(body)

    def delete_keys(self, keys: Iterable[str]) -> list[str]:
        """Batch-delete up to 1000 keys per call. Returns the keys deleted.

        R2 bills DELETE as free, but it is still counted so the monthly figure
        reflects every operation we perform.
        """
        keys = list(keys)
        deleted: list[str] = []
        for i in range(0, len(keys), 1000):
            chunk = keys[i:i + 1000]
            resp = self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )
            self._on_class_a(1)
            for err in resp.get("Errors", []):
                logger.warning("Failed to delete %s: %s",
                               self._safe(err.get("Key", "?")), err.get("Code"))
            failed = {e.get("Key") for e in resp.get("Errors", [])}
            deleted.extend(k for k in chunk if k not in failed)
        if deleted:
            logger.info("Deleted %d object(s) from R2", len(deleted))
        return deleted
