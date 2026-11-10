"""Side effects leaving the system: email, publishing the website, payment links.

Each one has a local stand-in so the whole flow can run on a laptop:
emails land in the store's outbox/ folder and the site is written to SITE_DIR.
"""

from __future__ import annotations

import re
import time
from email.message import EmailMessage
from urllib.parse import quote, urlencode

from .config import settings
from .site import build_site_files, content_type, write_local
from .store import Store


# ---------- email ----------

def send_email(store: Store, to: str | None, subject: str, text: str) -> str:
    """Send a plain-text email via SES, or drop it in the outbox when SES isn't set up.

    Returns where it went ("ses:<message id>" or the outbox key).
    """
    cfg = settings()
    if not to:
        return "skipped: no recipient configured"
    if cfg.from_email:
        import boto3

        resp = boto3.client("sesv2").send_email(
            FromEmailAddress=cfg.from_email,
            Destination={"ToAddresses": [to]},
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": text}}}},
        )
        return f"ses:{resp['MessageId']}"
    msg = EmailMessage()
    msg["From"] = "poker-bot@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    key = f"outbox/{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', subject.lower())[:40]}.eml"
    store.put_bytes(key, bytes(msg), "message/rfc822")
    return key


# ---------- website ----------

def publish_site(store: Store) -> str:
    cfg = settings()
    files = build_site_files(store)
    if not cfg.site_bucket:
        write_local(files, cfg.site_dir)
        return f"wrote {len(files)} files to {cfg.site_dir}"
    import boto3

    s3 = boto3.client("s3")
    for path, data in files.items():
        cache = "no-cache" if path.startswith("data/") or path == "index.html" else "public, max-age=300"
        s3.put_object(Bucket=cfg.site_bucket, Key=path, Body=data, ContentType=content_type(path), CacheControl=cache)
    if cfg.distribution_id:
        boto3.client("cloudfront").create_invalidation(
            DistributionId=cfg.distribution_id,
            InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": str(time.time())},
        )
    return f"published {len(files)} files"


# ---------- payment links ----------

def venmo_username(value: str) -> str | None:
    """Best guess at a Venmo username from the free-form Player List column."""
    value = (value or "").strip()
    if not value:
        return None
    m = re.search(r"@([A-Za-z0-9_-]{3,})", value)
    if m:
        return m.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{3,}", value) else None


def venmo_pay_link(username: str, amount: float, note: str) -> str:
    return f"https://venmo.com/{quote(username)}?" + urlencode({"txn": "pay", "amount": f"{amount:.2f}", "note": note})


def paypal_link(value: str, amount: float) -> str | None:
    """PayPal.me link when the column holds a PayPal.me name (emails can't be linked)."""
    value = (value or "").strip()
    m = re.search(r"paypal\.me/([A-Za-z0-9_-]+)", value)
    if m:
        return f"https://paypal.me/{m.group(1)}/{amount:.2f}"
    return None
