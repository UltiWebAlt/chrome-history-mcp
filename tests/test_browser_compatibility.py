import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import anyio
from click.testing import CliRunner

from chrome_history_mcp import server


class BrowserCompatibilityTests(unittest.TestCase):
    def test_default_paths(self):
        locations = {
            'Windows': {
                'chrome': 'Google/Chrome/User Data',
                'brave': 'BraveSoftware/Brave-Browser/User Data',
                'edge': 'Microsoft/Edge/User Data',
            },
            'Darwin': {
                'chrome': 'Google/Chrome',
                'brave': 'BraveSoftware/Brave-Browser',
                'edge': 'Microsoft Edge',
            },
            'Linux': {
                'chrome': 'google-chrome',
                'brave': 'BraveSoftware/Brave-Browser',
                'edge': 'microsoft-edge',
            },
        }
        for system, browsers in locations.items():
            root = Path('/users/test')
            if system == 'Darwin':
                root /= 'Library/Application Support'
            elif system == 'Linux':
                root /= '.config'
            for browser, location in browsers.items():
                with self.subTest(system=system, browser=browser), patch.object(
                    server.platform, 'system', return_value=system
                ), patch.object(server.Path, 'home', return_value=Path('/users/test')), patch.dict(
                    server.os.environ, {'LOCALAPPDATA': '/users/test'}, clear=True
                ):
                    self.assertEqual(
                        server.default_history_path(browser), root / location / 'Default/History'
                    )

    def test_linux_config_override(self):
        with patch.object(server.platform, 'system', return_value='Linux'), patch.dict(
            server.os.environ, {'XDG_CONFIG_HOME': '/custom/config'}
        ):
            self.assertEqual(server.default_history_path('edge'), Path('/custom/config/microsoft-edge/Default/History'))

    def test_browser_selection_and_snapshot_isolation(self):
        runner = CliRunner()
        snapshots = []
        run_async = anyio.run
        with tempfile.TemporaryDirectory() as directory:
            for browser in ('chrome', 'brave', 'edge'):
                history = Path(directory) / browser / 'History'
                history.parent.mkdir()
                with closing(sqlite3.connect(history)) as connection:
                    with connection:
                        connection.execute('CREATE TABLE urls (url TEXT)')
                        connection.execute('INSERT INTO urls VALUES (?)', (f'https://{browser}.example',))

                def run_query(_):
                    snapshots.append(server.history_file_tmp)
                    result = run_async(server.fetch_from_sqlite, 'SELECT url FROM urls')
                    self.assertEqual(result[0].text, f'url: https://{browser}.example')

                with patch.object(server, 'default_history_path', return_value=history) as resolve, patch.object(
                    server.anyio, 'run', side_effect=run_query
                ):
                    result = runner.invoke(server.main, ['--browser', browser])
                    self.assertEqual(result.exit_code, 0, result.output or str(result.exception))
                    resolve.assert_called_once_with(browser)
                self.assertFalse(Path(snapshots[-1]).exists())
        self.assertEqual(len(set(snapshots)), 3)

    def test_explicit_path_overrides_browser_and_os(self):
        with tempfile.NamedTemporaryFile() as history, patch.object(
            server, 'default_history_path', side_effect=AssertionError('must not resolve')
        ), patch.object(server.anyio, 'run'):
            result = CliRunner().invoke(server.main, ['--browser', 'BRAVE', '--path', history.name])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(server.history_file_original, history.name)

    def test_invalid_browser_and_missing_history(self):
        self.assertNotEqual(CliRunner().invoke(server.main, ['--browser', 'firefox']).exit_code, 0)
        with patch.object(server, 'default_history_path', return_value=Path('/missing/History')):
            result = CliRunner().invoke(server.main, ['--browser', 'edge'])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn('Specify --path', result.output)

    def test_unsupported_os_and_missing_windows_environment(self):
        for system in ('Unsupported', 'Windows'):
            with patch.object(server.platform, 'system', return_value=system), patch.dict(server.os.environ, {}, clear=True):
                result = CliRunner().invoke(server.main)
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn('--path', result.output)
