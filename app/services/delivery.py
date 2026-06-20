"""Email adapter — Resend in production, console stub in dev (plan B7).

send_email returns True on success. With no RESEND_API_KEY it logs the message
and returns True, so the whole notification path is testable without a key.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("trackbit.delivery")

_RESEND_URL = "https://api.resend.com/emails"


def send_email(
    *, to: str, subject: str, body: str, html: str | None = None, sender: str | None = None
) -> bool:
    """Send one email. `sender` overrides the from-identity; defaults to the
    general RESEND_FROM (e.g. pass RESEND_FROM_LOGIN for account-access mails).
    `body` is the plain-text fallback; `html` (optional) is the rich version —
    we send both so every client renders well."""
    if not settings.RESEND_API_KEY:
        logger.info("EMAIL (stub) → %s | %s\n%s", to, subject, body)
        return True
    payload = {
        "from": sender or settings.RESEND_FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if html:
        payload["html"] = html
    try:
        resp = httpx.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json=payload,
            timeout=10.0,
        )
        if resp.status_code >= 400:
            logger.warning("Resend send failed %s: %s", resp.status_code, resp.text)
            return False
        return True
    except httpx.HTTPError as exc:
        logger.warning("Resend request error: %s", exc)
        return False
