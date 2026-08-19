"""提供渠道客户端共用的有限响应体读取。"""

from typing import Final, cast

import httpx


async def read_limited_response(response: httpx.Response, max_bytes: int) -> bytes | None:
    declared_length: Final = cast(str | None, response.headers.get("content-length"))
    if declared_length is not None:
        try:
            if int(declared_length) > max_bytes:
                return None
        except ValueError:
            return None
    content: Final = bytearray()  # mutable-ok: 有界流式读取必须逐块聚合
    async for chunk in response.aiter_bytes():
        if len(content) + len(chunk) > max_bytes:
            return None
        content.extend(chunk)
    return bytes(content)
