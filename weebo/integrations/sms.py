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
    return Outcome(f"Text sent to {recipient} (Twilio id {sent.get('sid', '?')}).")
