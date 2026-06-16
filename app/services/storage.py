"""Object storage adapter (plan P4-BE-02).

One interface, two backends: Cloudflare R2 (S3-compatible, via boto3) when
configured, else a local-disk fallback so attachments work in dev without any
credentials. Photos are downscaled on upload to keep objects small.
"""

import io
import logging
import uuid
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("trackbit.storage")

_MAX_DIM = 1600  # px; downscale the long edge on upload


def make_key(*, org_id: uuid.UUID, instance_id: uuid.UUID, filename: str) -> str:
    ext = Path(filename or "").suffix.lower() or ".bin"
    return f"{org_id}/{instance_id}/{uuid.uuid4().hex}{ext}"


def maybe_downscale(data: bytes, content_type: str) -> bytes:
    """Best-effort image shrink. No-op if Pillow is absent or it isn't an image."""
    if not content_type.startswith("image/"):
        return data
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img.thumbnail((_MAX_DIM, _MAX_DIM))
        out = io.BytesIO()
        fmt = (img.format or "JPEG")
        if fmt.upper() in ("JPG", "JPEG"):
            img = img.convert("RGB")
        img.save(out, format=fmt)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — never block an upload on resize
        logger.exception("downscale failed; storing original")
        return data


def save_bytes(key: str, data: bytes, content_type: str) -> str:
    """Persist bytes and return a fetchable URL."""
    if settings.storage_configured:
        return _r2_put(key, data, content_type)
    return _local_put(key, data)


def _local_put(key: str, data: bytes) -> str:
    path = Path(settings.MEDIA_DIR) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"{settings.MEDIA_BASE_URL.rstrip('/')}/{key}"


def _r2_put(key: str, data: bytes, content_type: str) -> str:
    try:
        import boto3  # noqa: PLC0415
    except ImportError as exc:  # configured but SDK missing
        raise RuntimeError("boto3 is required for R2 storage. `uv add boto3`.") from exc
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    client.put_object(Bucket=settings.R2_BUCKET, Key=key, Body=data, ContentType=content_type)
    base = settings.R2_PUBLIC_BASE_URL.rstrip("/")
    if base:
        return f"{base}/{key}"
    # No public base configured -> hand back a time-limited presigned GET.
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": settings.R2_BUCKET, "Key": key}, ExpiresIn=3600
    )
