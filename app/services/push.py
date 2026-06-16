"""Web Push adapter (VAPID). Returns (ok, gone): gone=True means purge the token."""

import json
import logging

from pywebpush import WebPushException, webpush

from app.core.config import settings

logger = logging.getLogger("trackbit.push")


def push_send(*, subscription: dict, title: str, body: str, url: str) -> tuple[bool, bool]:
    if not settings.VAPID_PRIVATE_KEY:
        logger.info("PUSH (stub) → %s\n%s", title, body)
        return True, False
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
        )
        return True, False
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        gone = status in (404, 410)  # subscription expired/unsubscribed
        if not gone:
            logger.warning("Push send failed: %s", exc)
        return False, gone
