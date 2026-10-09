import unittest
from unittest.mock import patch, AsyncMock
from click.testing import CliRunner
import anyio
import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from chrome_history_mcp.network import create_http_app
from chrome_history_mcp import server


class NetworkTests(unittest.TestCase):
    def exercise(self, token=None):
        async def run():
            from sse_starlette.sse import AppStatus
            AppStatus.should_exit_event = None
            app = Server('test')
            @app.list_tools()
            async def tools():
                return [types.Tool(name='example', description='test', inputSchema={'type': 'object'})]
            web = create_http_app(app, '127.0.0.1', 8765, token=token)
            async with web.router.lifespan_context(web):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url='http://127.0.0.1:8765') as client:
                    headers={'Accept': 'application/json, text/event-stream'}
                    request={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}}
                    if token:
                        response=await client.post('/mcp', json=request, headers=headers)
                        self.assertEqual(response.status_code, 401)
                        headers['Authorization']='Bearer ' + token
                    response=await client.post('/mcp', json=request, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn('test', response.text)
                    headers['mcp-session-id']=response.headers['mcp-session-id']
                    await client.post('/mcp', json={'jsonrpc':'2.0', 'method':'notifications/initialized'}, headers=headers)
                    response=await client.post('/mcp', json={'jsonrpc':'2.0','id':2,'method':'tools/list'}, headers=headers)
                    self.assertIn('example', response.text)
                    response=await client.post('/mcp', json=request, headers={**headers,'Host':'evil.example:8765'})
                    self.assertEqual(response.status_code, 421)
                    response=await client.post('/mcp', json=request, headers={**headers,'Origin':'http://evil.example'})
                    self.assertEqual(response.status_code, 400)
                    response = await client.delete('/mcp', headers=headers)
                    self.assertEqual(response.status_code, 200)
        anyio.run(run)

    def test_initialize_and_discover_tools_over_http(self):
        self.exercise()

    def test_optional_bearer_token(self):
        self.exercise('secret')

    def test_invalid_port_is_rejected(self):
        result=CliRunner().invoke(server.main, ['--transport','http','--port','65536'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('65535', result.output)


    def test_cli_passes_http_configuration(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / 'History'
            history.touch()
            with patch('chrome_history_mcp.network.serve_http', new_callable=AsyncMock) as serve:
                result = CliRunner().invoke(server.main, [
                    '--path', str(history), '--transport', 'http',
                    '--host', '100.101.102.103', '--port', '9000',
                    '--allowed-host', 'desktop:9000', '--http-token', 'secret',
                ])
            self.assertEqual(result.exit_code, 0, result.exception)
            serve.assert_awaited_once()
            self.assertEqual(serve.call_args.args[1:], ('100.101.102.103', 9000, ('desktop:9000',), 'secret'))
