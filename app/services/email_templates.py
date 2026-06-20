"""Branded TrackBit email rendering.

Every outbound email is built here so the look stays consistent and professional.
`render_email` returns both an HTML body and a plain-text fallback (we send both —
rich clients show the HTML, everything else degrades to clean text).

The HTML is intentionally old-school: table-based layout with fully inline styles,
because that's the only thing email clients (Gmail, Outlook, Apple Mail) render
reliably. Brand colors mirror the web app's theme (globals.css).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

# Brand palette (kept in sync with trackbit_web/src/app/globals.css).
PRIMARY = "#2f6f4f"        # calm forest green — the brand color
WARNING = "#b5791f"        # gentle amber — overdue, never alarming red
INK = "#1c1b19"            # near-black heading ink
BODY_INK = "#44433d"       # softer body text
MUTED = "#8a8980"          # footer / secondary text
HAIRLINE = "#f0efea"       # subtle divider
CARD_BORDER = "#e6e4dd"
PAGE_BG = "#f4f4f2"

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
    "'Apple Color Emoji','Segoe UI Emoji',sans-serif"
)


@dataclass
class Email:
    """A fully rendered message ready to hand to the delivery adapter."""

    subject: str
    html: str
    text: str


def _paragraphs_html(paragraphs: Sequence[str]) -> str:
    out = []
    for p in paragraphs:
        safe = escape(p).replace("\n", "<br>")
        out.append(
            f'<p style="margin:0 0 14px 0;font-size:15px;line-height:1.65;'
            f'color:{BODY_INK};">{safe}</p>'
        )
    return "".join(out)


def _cta_html(label: str, url: str, accent: str) -> str:
    safe_label = escape(label)
    safe_url = escape(url, quote=True)
    return (
        f'<tr><td style="padding:8px 32px 4px 32px;font-family:{_FONT};">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="border-radius:10px;background:{accent};">'
        f'<a href="{safe_url}" target="_blank" '
        f'style="display:inline-block;padding:13px 28px;font-size:15px;font-weight:600;'
        f'color:#ffffff;text-decoration:none;border-radius:10px;font-family:{_FONT};">'
        f"{safe_label}</a>"
        f"</td></tr></table>"
        f'<p style="margin:14px 0 0 0;font-size:13px;line-height:1.5;color:{MUTED};">'
        f"Or paste this link into your browser:<br>"
        f'<a href="{safe_url}" target="_blank" '
        f'style="color:{PRIMARY};text-decoration:underline;word-break:break-all;">'
        f"{escape(url)}</a></p>"
        f"</td></tr>"
    )


def render_email(
    *,
    heading: str,
    paragraphs: Sequence[str],
    cta_label: str | None = None,
    cta_url: str | None = None,
    footer_note: str | None = None,
    preheader: str | None = None,
    accent: str = PRIMARY,
) -> tuple[str, str]:
    """Build (html, text) for a branded TrackBit email."""
    safe_heading = escape(heading)
    preheader_text = escape(preheader or heading)

    cta_block = _cta_html(cta_label, cta_url, accent) if cta_label and cta_url else ""

    footer_block = ""
    if footer_note:
        footer_block = (
            f'<p style="margin:0 0 10px 0;font-size:13px;line-height:1.55;color:{MUTED};">'
            f"{escape(footer_note)}</p>"
        )

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="x-apple-disable-message-reformatting">
<title>{safe_heading}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE_BG};">
<span style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;mso-hide:all;">{preheader_text}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE_BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background:#ffffff;border:1px solid {CARD_BORDER};border-radius:14px;overflow:hidden;">
<tr><td style="height:4px;line-height:4px;font-size:4px;background:{accent};">&nbsp;</td></tr>
<tr><td style="padding:26px 32px 0 32px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="vertical-align:middle;width:36px;height:36px;background:{PRIMARY};border-radius:9px;text-align:center;color:#ffffff;font-family:{_FONT};font-size:20px;font-weight:700;line-height:36px;">&#10003;</td>
<td style="vertical-align:middle;padding-left:11px;font-family:{_FONT};font-size:18px;font-weight:700;color:{INK};letter-spacing:-0.2px;">TrackBit</td>
</tr></table>
</td></tr>
<tr><td style="padding:24px 32px 4px 32px;font-family:{_FONT};">
<h1 style="margin:0 0 14px 0;font-size:21px;line-height:1.3;color:{INK};font-weight:700;">{safe_heading}</h1>
{_paragraphs_html(paragraphs)}
</td></tr>
{cta_block}
<tr><td style="padding:26px 32px 30px 32px;border-top:1px solid {HAIRLINE};font-family:{_FONT};">
{footer_block}
<p style="margin:0;font-size:12px;line-height:1.5;color:{MUTED};">TrackBit · simple, stress-free task management for small teams.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    # Plain-text fallback.
    lines = [heading, ""]
    lines += list(paragraphs)
    if cta_label and cta_url:
        lines += ["", f"{cta_label}: {cta_url}"]
    if footer_note:
        lines += ["", footer_note]
    lines += ["", "—", "TrackBit · simple, stress-free task management for small teams."]
    text = "\n".join(lines)

    return html, text


# ── Scenario builders ──────────────────────────────────────────────────────
# One function per email we send, so the copy lives in one place.

def invite(*, org_name: str, inviter_name: str, url: str) -> Email:
    html, text = render_email(
        heading=f"Welcome to {org_name}",
        paragraphs=[
            f"{inviter_name} added you to {org_name} on TrackBit — a calm, "
            "stress-free way to keep your team's tasks on track.",
            "Tap below to set up your account and jump in. It only takes a minute.",
        ],
        cta_label="Get started",
        cta_url=url,
        footer_note="If you weren't expecting this, you can safely ignore this email.",
        preheader=f"{inviter_name} added you to {org_name} on TrackBit.",
    )
    return Email(
        subject=f"You've been added to {org_name} on TrackBit", html=html, text=text
    )


def password_reset(*, url: str, by_admin: bool) -> Email:
    if by_admin:
        intro = (
            "An admin started a password reset for your TrackBit account. "
            "Choose a new password to get back in."
        )
    else:
        intro = "We received a request to reset the password for your TrackBit account."
    html, text = render_email(
        heading="Reset your password",
        paragraphs=[
            intro,
            "This link will expire in 24 hours for your security.",
        ],
        cta_label="Choose a new password",
        cta_url=url,
        footer_note=(
            "Didn't request this? You can ignore this email — your password "
            "won't change until you use the link above."
        ),
        preheader="Choose a new password for your TrackBit account.",
    )
    return Email(subject="Reset your TrackBit password", html=html, text=text)


# notif_type → (accent color, call-to-action label) for notification emails.
_NOTIF_STYLE: dict[str, tuple[str, str]] = {
    "overdue": (WARNING, "Open task"),
    "digest": (PRIMARY, "Open TrackBit"),
    "report_card": (PRIMARY, "View dashboard"),
}


def notification(*, notif_type: str, heading: str, message: str, url: str) -> tuple[str, str]:
    """Render a notification email (overdue / digest / report_card). Returns (html, text)."""
    accent, cta_label = _NOTIF_STYLE.get(notif_type, (PRIMARY, "Open in TrackBit"))
    return render_email(
        heading=heading,
        paragraphs=[message],
        cta_label=cta_label,
        cta_url=url,
        footer_note="You're receiving this because you're a member of a TrackBit workspace.",
        preheader=message,
        accent=accent,
    )
