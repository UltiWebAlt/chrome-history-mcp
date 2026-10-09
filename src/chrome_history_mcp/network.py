"""Streamable HTTP transport for local and private-network MCP clients."""
from contextlib import asynccontextmanager
import secrets

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import PlainTextResponse
import uvicorn


def create_http_app(server, host, port, allowed_hosts=(), token=None):
    authority = f'[{host}]' if ':' in host and not host.startswith('[') else host
    hosts = list(dict.fromkeys([f'{authority}:{port}', 'localhost:' + str(port), *allowed_hosts]))
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f'{scheme}://{name}' for name in hosts for scheme in ('http', 'https')],
    )
    manager = StreamableHTTPSessionManager(server, security_settings=security)

    async def endpoint(scope, receive, send):
        if token:
            headers = dict(scope.get('headers', []))
            supplied = headers.get(b'authorization', b'')
            if not secrets.compare_digest(supplied, ('Bearer ' + token).encode('utf-8')):
                response = PlainTextResponse('Unauthorized', status_code=401)
                await response(scope, receive, send)
                return
        await manager.handle_request(scope, receive, send)

    class MCPEndpoint:
        async def __call__(self, scope, receive, send):
            await endpoint(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    return Starlette(routes=[Route('/mcp', endpoint=MCPEndpoint(), methods=['GET', 'POST', 'DELETE'])], lifespan=lifespan)


async def serve_http(server, host, port, allowed_hosts=(), token=None):
    app = create_http_app(server, host, port, allowed_hosts, token)
    config = uvicorn.Config(app, host=host, port=port, log_level='info', access_log=False)
    await uvicorn.Server(config).serve()
