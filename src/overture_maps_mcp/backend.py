"""Private service transport for the Sites Worker; this is not an OAuth resource server."""

from __future__ import annotations

import secrets

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from overture_maps_mcp.server import mcp


def create_backend(token: str) -> ASGIApp:
    if len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token):
        raise ValueError("OVERTURE_BACKEND_TOKEN must be an ASCII secret of at least 32 characters")
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=256 * 1024,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*"], allowed_origins=[]
        ),
    )

    async def authenticated(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            supplied = Headers(scope=scope).get("authorization", "")
            if not secrets.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                await Response("Unauthorized", status_code=401)(scope, receive, send)
                return
            if scope["path"] != "/mcp":
                await Response("Not found", status_code=404)(scope, receive, send)
                return
            if scope["method"] != "POST":
                await Response("Use POST /mcp", status_code=405, headers={"Allow": "POST"})(
                    scope, receive, send
                )
                return
        await app(scope, receive, send)

    return authenticated
