"""Public-page text extraction and a private local SQLite cache."""
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import sqlite3
import ssl
import time
import subprocess
import sys
import json
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_DOWNLOAD_BYTES = 1_000_000
MAX_TEXT_CHARS = 100_000
FETCH_TIMEOUT = 4
VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
EXCLUDED_TAGS = {'script', 'style', 'noscript', 'nav', 'header', 'footer', 'form', 'svg', 'template', 'head'}


class ReadableText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (tag in EXCLUDED_TAGS or 'hidden' in attrs
                  or attrs.get('aria-hidden', '').lower() == 'true'
                  or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', attrs.get('style', ''), re.I)))
        blocked = hidden or (self.stack and self.stack[-1][1])
        if tag not in VOID_TAGS:
            self.stack.append((tag, bool(blocked)))
        if tag in {'p', 'div', 'article', 'section', 'br', 'li', 'h1', 'h2', 'h3'}:
            self.parts.append(' ')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        self.parts.append(' ')

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)


def extract_text(html):
    parser = ReadableText()
    parser.feed(html)
    return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()[:MAX_TEXT_CHARS]


def public_target(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Only public HTTP/HTTPS pages can be indexed')
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('URLs with credentials are not indexed')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in {80, 443}:
        raise ValueError('Only standard public web ports are indexed')
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global or ipaddress.ip_address(a[4][0]).is_multicast for a in addresses):
        raise ValueError('Private, loopback, and non-public network addresses are not indexed')
    return parsed, port, addresses[0][4][0]


def _fetch_public_text(url):
    """No browser cookies; validate every redirect and pin the validated address."""
    deadline = time.monotonic() + FETCH_TIMEOUT
    for _ in range(5):
        parsed, port, address = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Page download timed out')
        if parsed.scheme == 'https':
            conn = http.client.HTTPSConnection(parsed.hostname, port, timeout=remaining, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parsed.hostname, port, timeout=remaining)
        # DNS is checked once above. Keep the TLS hostname for certificate validation.
        conn._create_connection = lambda *args, **kwargs: socket.create_connection((address, port), timeout=remaining)
        try:
            target = parsed.path or '/'
            if parsed.query:
                target += '?' + parsed.query
            conn.request('GET', target, headers={'User-Agent': 'LocalHistoryContentIndexer/0.1', 'Accept': 'text/html,text/plain', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Redirect has no location')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f'HTTP {response.status}')
            content_type = response.getheader('Content-Type', '')
            mime = content_type.split(';')[0].strip().lower()
            if mime not in {'text/html', 'application/xhtml+xml', 'text/plain'}:
                raise ValueError('Only HTML and plain text pages are supported')
            encoding_match = re.search(r'charset\s*=\s*["\']?([^;\s"\']+)', content_type, re.I)
            encoding = encoding_match.group(1) if encoding_match else 'utf-8'
            data = bytearray()
            while len(data) <= MAX_DOWNLOAD_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Page download timed out')
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_DOWNLOAD_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError('Page exceeds the 1 MB download limit')
            try:
                decoded = data.decode(encoding, errors='replace')
            except LookupError:
                decoded = data.decode('utf-8', errors='replace')
            text = extract_text(decoded) if mime != 'text/plain' else re.sub(r'\s+', ' ', decoded).strip()[:MAX_TEXT_CHARS]
            if not text:
                raise ValueError('No readable page text found')
            return text, url
        finally:
            conn.close()
    raise ValueError('Too many redirects')


def fetch_public_text(url):
    """A subprocess makes DNS, TLS, and header stalls killable at the deadline."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "chrome_history_mcp.content", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=FETCH_TIMEOUT + 1, check=False,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("Page download exceeded its five-second deadline") from None
    if completed.returncode != 0:
        raise ValueError("Public page download worker failed")
    result = json.loads(completed.stdout)
    if "error" in result:
        raise ValueError(result["error"])
    return result["text"], result["url"]


def cache_path(history_path):
    root = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    profile = hashlib.sha256(str(Path(history_path).resolve()).encode()).hexdigest()[:16]
    return root / 'chrome-history-mcp' / f'content-{profile}.sqlite'


def open_cache(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(path, timeout=10)
    path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, text TEXT NOT NULL, fetched_at TEXT NOT NULL, status TEXT NOT NULL, error TEXT, final_url TEXT)')
    connection.commit()
    return connection


def read_cache(path):
    if not Path(path).exists():
        return {}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pages'").fetchone():
            return {}
        return {row['url']: dict(row) for row in conn.execute('SELECT * FROM pages')}


def store_result(connection, url, text='', error=None, final_url=None):
    connection.execute('INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?)',
                       (url, text, datetime.now(timezone.utc).isoformat(), 'error' if error else 'ok', error, final_url))
    connection.commit()


if __name__ == "__main__":
    try:
        text, final_url = _fetch_public_text(sys.argv[1])
        print(json.dumps({"text": text, "url": final_url}))
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, TimeoutError)) else type(error).__name__
        print(json.dumps({"error": message[:160]}))
