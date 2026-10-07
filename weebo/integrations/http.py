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


async def read_body(resp: aiohttp.ClientResponse, limit: int | None = None) -> tuple[bytes, bool]:
    """The whole body up to ``limit`` bytes (default MAX_BYTES), and whether it was cut off there.
    (``resp.content.read(n)`` returns only what has arrived so far, often a single network chunk.)"""
    limit = MAX_BYTES if limit is None else limit
    chunks, size = [], 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            return b"".join(chunks)[:limit], True
    return b"".join(chunks), False


async def get_text(url: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> str:
    """A page as text (pages over 8 MB are cut off; that's plenty for reading)."""
    async with aiohttp.ClientSession(timeout=_timeout(timeout), headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url, params=params) as resp:
            if resp.status >= 400:
                raise HttpError(f"{url} answered HTTP {resp.status}")
            body, _cut = await read_body(resp)
            return body.decode(resp.charset or "utf-8", errors="replace")


async def get_bytes(url: str, timeout: float = 15.0) -> tuple[bytes, str]:
    """Returns (body, content type). Refuses bodies over 8 MB rather than returning a broken prefix."""
    async with aiohttp.ClientSession(timeout=_timeout(timeout), headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise HttpError(f"{url} answered HTTP {resp.status}")
            body, cut = await read_body(resp)
            if cut:
                raise HttpError(f"{url} is larger than {MAX_BYTES // (1024 * 1024)} MB")
            return body, resp.content_type or ""


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
