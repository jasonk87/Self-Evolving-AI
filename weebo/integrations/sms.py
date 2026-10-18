"""SMS through the user's Twilio account (REST API, no SDK needed)."""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING

from . import http
from .base import IntegrationError, Outcome

if TYPE_CHECKING:
    from ..app import WeeboApp

PENDING_KEY = "sms.pending_delivery"
CHECK_DELAY = 60
MAX_CHECKS = 5
FAILED = {"failed", "undelivered", "canceled"}
DELIVERED = {"delivered", "read"}


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
    if status in FAILED or sent.get("error_code"):
        raise IntegrationError(f"Twilio didn't send it ({status or 'error'} {sent.get('error_code') or ''}).".strip())
    message_sid = str(sent.get("sid") or "")
    if status in DELIVERED:
        return Outcome(f"Text to {recipient}: Twilio reports {status} (id {message_sid or '?'}).")
    # Track only a returned identifier, never repeat the POST after uncertainty.
    if re.fullmatch(r"SM[A-Za-z0-9]+", message_sid):
        pending = app.store.kv_get(PENDING_KEY, {}) or {}
        pending[message_sid] = {"recipient": recipient, "account": sid, "attempts": 0,
                                "next_check": time.time() + CHECK_DELAY}
        app.store.kv_set(PENDING_KEY, pending)
        return Outcome(f"Text to {recipient} accepted by Twilio (id {message_sid}, status {status or 'unknown'}). "
                       "Delivery is not confirmed; I'll check in about a minute.")
    return Outcome(f"Twilio accepted the request for {recipient}, but returned no usable message ID. "
                   "Delivery is unverified; don't resend automatically.")


async def check_deliveries(app: "WeeboApp", now: float | None = None) -> None:
    """Bounded, restart-safe read-only polling. Results enter the chief's chat, never a bot notification."""
    now = time.time() if now is None else now
    pending = app.store.kv_get(PENDING_KEY, {}) or {}
    for message_sid, entry in list(pending.items())[:20]:
        if entry["next_check"] > now:
            continue
        status, error = "unknown", ""
        sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
        try:
            if sid != entry["account"] or not token:
                raise IntegrationError("The original Twilio account is not available.")
            reply = await http.get_json(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages/{message_sid}.json",
                                        auth=(sid, token))
            if reply.get("sid") != message_sid:
                raise IntegrationError("Twilio returned a different message ID.")
            status = str(reply.get("status") or "unknown")
            if reply.get("error_code"):
                error = f"provider error {reply['error_code']}"
                status = "failed"
        except Exception as exc:
            # Do not copy provider URLs, response bodies or credentials into the chat.
            error = f"status lookup unavailable ({type(exc).__name__})"
        entry = {**entry, "attempts": entry["attempts"] + 1, "next_check": now + CHECK_DELAY}
        terminal = status in DELIVERED | FAILED or entry["attempts"] >= MAX_CHECKS
        current = app.store.kv_get(PENDING_KEY, {}) or {}  # sends may have arrived while awaiting the read
        if terminal:
            current.pop(message_sid, None)
            outcome = "delivered" if status in DELIVERED else "failed" if status in FAILED else "unverified"
            text = (f"Text to {entry['recipient']}: " +
                    (f"Twilio reports {status}." if outcome != "unverified" else
                     f"delivery is still unverified after {entry['attempts']} checks (status {status}).") +
                    (f" {error}." if error else "") + (" No automatic resend." if outcome != "delivered" else ""))
            main = app.conversations.main_id()
            message = app.store.upsert_message(f"sms:{message_sid}", main, "event", "notice", text,
                                               {"sms_id": message_sid, "delivery": outcome, "status": status}, None)
            app.bus.publish("conv.message", {"conversation_id": main, "message": message})
            app.conversations.add_context(main, text)
            app.store.journal("sms.delivery", f"SMS {outcome}", text, data={"sms_id": message_sid})
        else:
            current[message_sid] = entry
        app.store.kv_set(PENDING_KEY, current)
