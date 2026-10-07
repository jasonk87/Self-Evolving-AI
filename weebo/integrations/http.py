"""Small async HTTP helpers shared by the integrations (one place to stub in tests)."""

from __future__ import annotations

from typing import Any

import aiohttp

USER_AGENT = "Mozilla/5.0 (compatible; Weebo/2.0; +https://github.com/jasonk87/self-evolving-ai)"
MAX_BYTES = 8 * 1024 * 1024


class HttpError(RuntimeError):
    pass


def _timeout(seconds: float) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=seconds)


async def get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    async with aiohttp.ClientSession(timeout=_timeout(timeout), headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url, params=params) as resp:
            if resp.status >= 400:
                raise HttpError(f"{url.split('?')[0]} answered HTTP {resp.status}: {(await resp.text())[:300]}")
            return await resp.json(content_type=None)


async def get_text(url: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> str:
    async with aiohttp.ClientSession(timeout=_timeout(timeout), headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url, params=params) as resp:
            if resp.status >= 400:
                raise HttpError(f"{url} answered HTTP {resp.status}")
            body = await resp.content.read(MAX_BYTES)
            return body.decode(resp.charset or "utf-8", errors="replace")


async def get_bytes(url: str, timeout: float = 15.0) -> tuple[bytes, str]:
    """Returns (body, content type)."""
    async with aiohttp.ClientSession(timeout=_timeout(timeout), headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise HttpError(f"{url} answered HTTP {resp.status}")
            return await resp.content.read(MAX_BYTES), resp.content_type or ""


async def post_form(url: str, data: dict[str, str], auth: tuple[str, str] | None = None,
                    timeout: float = 20.0) -> Any:
    basic = aiohttp.BasicAuth(*auth) if auth else None
    async with aiohttp.ClientSession(timeout=_timeout(timeout), headers={"User-Agent": USER_AGENT}) as session:
        async with session.post(url, data=data, auth=basic) as resp:
            payload = await resp.json(content_type=None)
            if resp.status >= 400:
                message = payload.get("message") if isinstance(payload, dict) else str(payload)[:300]
                raise HttpError(f"HTTP {resp.status}: {message}")
            return payload
