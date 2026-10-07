"""Shared types for integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class IntegrationError(RuntimeError):
    """A user-facing failure (bad arguments, service said no); reported without a stack trace."""


@dataclass
class Outcome:
    text: str  # what the model sees
    extras: dict[str, Any] = field(default_factory=dict)  # html / images for the chat UI
