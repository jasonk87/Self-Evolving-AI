"""SMS through the user's Twilio account (REST API, no SDK needed)."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from . import http
from .base import IntegrationError, Outcome

if TYPE_CHECKING:
    from ..app import WeeboApp


async def send_text_message(app: "WeeboApp", recipient: str, message: str) -> Outcome:
    recipient = re.sub(r"[\s().-]", "", recipient or "")
    if not re.fullmatch(r"\+?\d{7,15}", recipient):
        raise IntegrationError("recipient must be a phone number, e.g. +15551234567.")
    if not (message or "").strip():
        raise IntegrationError("message is empty.")
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    sent = await http.post_form(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                                {"To": recipient, "From": os.environ["TWILIO_PHONE_NUMBER"], "Body": message[:1600]},
                                auth=(sid, token))
    # Verify after acting: Twilio's own answer says whether it took the message; a request that returned is not proof.
    status = str(sent.get("status") or "")
    if status in ("failed", "undelivered", "canceled") or sent.get("error_code"):
        raise IntegrationError(f"Twilio didn't send it ({status or 'error'} {sent.get('error_code') or ''}).".strip())
    if status:
        return Outcome(f"Text to {recipient} accepted by Twilio (id {sent.get('sid', '?')}, status {status}).")
    return Outcome(f"Text sent to {recipient} (Twilio id {sent.get('sid', '?')}); Twilio didn't report a status, so "
                   "delivery isn't verified.")
