import asyncio
from unittest.mock import AsyncMock

async def main():
    m = AsyncMock()

    async def side_effect(*args, **kwargs):
        if isinstance(m.return_value, str):
            return m.return_value
        return "default"

    m.side_effect = side_effect

    print("before:", await m())
    m.return_value = "hello"
    print("after:", await m())

asyncio.run(main())
