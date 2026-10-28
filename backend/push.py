"""Web Push notifications via VAPID.

Endpoints:
    POST /api/push/subscribe     — store a PushSubscription from the browser
    POST /api/push/unsubscribe   — remove it (user toggled off, or uninstalled)

Sending:
    push.notify("New call from +91…", {"url": "/app/calls"})

Subscriptions that return 404 or 410 (Gone) are deleted automatically — the
browser unregistered them (cleared data, uninstalled the PWA, etc.).

Setup:
    python -m push generate-keys
    → prints VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY to paste into .env
"""

import json
import logging
import os
import sys

import db

log = logging.getLogger("voiceflow.push")

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS = {"sub": os.getenv("VAPID_CONTACT", "mailto:admin@voiceflow.local")}


def configured() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


# --- subscription storage (delegates to db) --------------------------------

def subscribe(subscription_json: dict) -> None:
    """Store a PushSubscription object from the browser."""
    endpoint = subscription_json.get("endpoint", "")
    if not endpoint:
        raise ValueError("subscription has no endpoint")
    db.upsert_push_subscription(endpoint, json.dumps(subscription_json))


def unsubscribe(endpoint: str) -> None:
    """Remove a subscription by its endpoint URL."""
    db.delete_push_subscription(endpoint)


# --- sending ----------------------------------------------------------------

def notify(title: str, body: str, data: dict | None = None, tag: str = "") -> int:
    """Push a notification to every registered subscription.

    Returns the number of successfully delivered pushes. Failed subscriptions
    (404/410) are deleted automatically.
    """
    if not configured():
        log.debug("push: VAPID keys not set, skipping")
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("pywebpush not installed — push notifications disabled")
        return 0

    subs = db.get_push_subscriptions()
    if not subs:
        return 0

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {},
        "tag": tag,
    })

    sent = 0
    for row in subs:
        try:
            sub_info = json.loads(row["subscription_json"])
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e, "response", None)
            status_code = status.status_code if status else 0
            if status_code in (404, 410):
                # Subscription is dead — browser cleared data or uninstalled PWA.
                log.info("removing dead push subscription (HTTP %d): %s…",
                         status_code, row["endpoint"][:60])
                db.delete_push_subscription(row["endpoint"])
            else:
                log.warning("push failed for %s…: %s", row["endpoint"][:60], e)
        except Exception as e:  # noqa: BLE001
            log.warning("push error: %s", e)

    return sent


# --- VAPID key generation CLI -----------------------------------------------

def _generate_keys():
    """Generate a VAPID key pair and print .env entries."""
    try:
        from py_vapid import Vapid
    except ImportError:
        print("Install py_vapid first:  pip install pywebpush")
        sys.exit(1)

    vapid = Vapid()
    vapid.generate_keys()

    # applicationServerKey the browser needs (URL-safe base64, no padding)
    import base64
    raw_public = vapid.public_key.public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization",
                            fromlist=["Encoding"]).Encoding.X962,
        format=__import__("cryptography.hazmat.primitives.serialization",
                          fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode()

    raw_private = vapid.private_key.private_numbers().private_value
    private_b64 = base64.urlsafe_b64encode(
        raw_private.to_bytes(32, "big")
    ).rstrip(b"=").decode()

    print("\n# Add these to backend/.env:\n")
    print(f"VAPID_PUBLIC_KEY={public_b64}")
    print(f"VAPID_PRIVATE_KEY={private_b64}")
    print(f"VAPID_CONTACT=mailto:you@example.com")
    print("\n# And set VAPID_PUBLIC_KEY in the frontend too (or it reads from /api/push/vapid-key).\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "generate-keys":
        _generate_keys()
    else:
        print("Usage:  python -m push generate-keys")
