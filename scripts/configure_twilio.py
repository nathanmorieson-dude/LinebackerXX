"""
configure_twilio.py — Point the Twilio AU number's voice webhook at this server.

Usage
-----
    # After starting ngrok manually:
    python scripts/configure_twilio.py --url https://abc123.ngrok-free.app

    # Or import and call from code (used by server.py lifespan):
    from scripts.configure_twilio import set_twilio_webhook
    set_twilio_webhook("https://abc123.ngrok-free.app/incoming")

Requirements
------------
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER must be in .env
  or already set in the environment.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def set_twilio_webhook(webhook_url: str) -> None:
    """
    Update the Twilio phone number's voice URL to webhook_url.

    If webhook_url does not end with /incoming, it is appended automatically.
    """
    try:
        from twilio.rest import Client
    except ImportError as exc:
        raise RuntimeError(
            "twilio package not installed. Run: pip install twilio"
        ) from exc

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    phone_number = os.environ.get("TWILIO_PHONE_NUMBER", "").strip()

    if not all([account_sid, auth_token, phone_number]):
        raise EnvironmentError(
            "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER "
            "must be set (in .env or environment)."
        )

    # Normalise URL — ensure it points to /incoming
    if not webhook_url.endswith("/incoming"):
        webhook_url = webhook_url.rstrip("/") + "/incoming"

    client = Client(account_sid, auth_token)

    # Find the phone number resource
    numbers = client.incoming_phone_numbers.list(phone_number=phone_number)
    if not numbers:
        raise ValueError(
            f"Phone number {phone_number} not found in Twilio account {account_sid}."
        )

    number = numbers[0]
    number.update(
        voice_url=webhook_url,
        voice_method="POST",
    )
    logger.info(
        "Twilio number %s voice_url updated to: %s",
        phone_number,
        webhook_url,
    )
    print(f"[configure_twilio] {phone_number} → {webhook_url}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Set Twilio voice webhook for the AU number in .env"
    )
    parser.add_argument(
        "--url",
        required=True,
        help=(
            "Public HTTPS URL of the running server. "
            "E.g. https://abc123.ngrok-free.app — /incoming is appended if absent."
        ),
    )
    args = parser.parse_args()

    try:
        set_twilio_webhook(args.url)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
