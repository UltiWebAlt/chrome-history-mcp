import anyio
import click
import mcp.types as types
from mcp.server.lowlevel import Server
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import tempfile

history_file_original = None
history_file_tmp = "chrome-history-snapshot"

def default_history_path(browser: str) -> Path:
    """Locate a stable Chromium browser's Default profile history."""
    system = platform.system().lower()
    browser_paths = {
        "windows": {
            "chrome": "Google/Chrome/User Data",
            "brave": "BraveSoftware/Brave-Browser/User Data",
            "edge": "Microsoft/Edge/User Data",
        },
        "darwin": {
            "chrome": "Google/Chrome",
            "brave": "BraveSoftware/Brave-Browser",
            "edge": "Microsoft Edge",
        },
        "linux": {
            "chrome": "google-chrome",
            "brave": "BraveSoftware/Brave-Browser",
            "edge": "microsoft-edge",
        },
    }
    if system not in browser_paths:
        raise click.ClickException(
            f"Unsupported operating system: {system}. Specify --path explicitly."
        )
    if system == "windows":
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise click.ClickException("LOCALAPPDATA is unset. Specify --path explicitly.")
        root = Path(local_app_data)
    elif system == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / browser_paths[system][browser] / "Default" / "History"


async def fetch_from_sqlite(
    sql_statement: str,
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    # Copy history file if updated
    try:
        if os.stat(history_file_original).st_mtime - os.stat(history_file_tmp).st_mtime > 1:
            shutil.copy2(history_file_original, history_file_tmp)
    except FileNotFoundError:
        shutil.copy2(history_file_original, history_file_tmp)

    conn = sqlite3.connect(history_file_tmp)
    c = conn.cursor()
    
    c.execute(sql_statement)
    column_names = [desc[0] for desc in c.description]

    results: list[types.TextContent] = []
    for row in c:
        row_dict = dict(zip(column_names, row))
        # Convert each row to a string representation
        row_str = ', '.join(f"{key}: {value}" for key, value in row_dict.items())
        results.append(types.TextContent(type='text', text=row_str))


    conn.close()
    return results


@click.command()
@click.option(
    "--browser",
    type=click.Choice(["chrome", "brave", "edge"], case_sensitive=False),
    default="chrome",
    show_default=True,
    help="Browser whose Default profile history to read; --path overrides its location.",
)
@click.option(
    "--path",
    required=False,
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a Chrome, Brave, or Microsoft Edge history file.",
)
def main(path: Path | None, browser: str) -> int:
    app = Server("chrome-history-mcp", version="0.1.0")

    @app.call_tool()
    async def fetch_tool(
        name: str, arguments: dict
    ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        if name != "fetch-urls-from-sqlite" and name != "fetch-visits-info-from-sqlite":
            raise ValueError(f"Unknown tool: {name}")
        
        if "sql_statement" not in arguments:
            raise ValueError("Missing required argument 'sql_statement'")
        return await fetch_from_sqlite(sql_statement=arguments["sql_statement"])


    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="fetch-urls-from-sqlite",
                description=
                '''
                Use SQL to query SQLite data tables that contain "URL" information of the selected browser history (Chrome, Brave, or Microsoft Edge). Table schema:
                CREATE TABLE urls(id INTEGER PRIMARY KEY AUTOINCREMENT,url LONGVARCHAR,title LONGVARCHAR,visit_count INTEGER DEFAULT 0 NOT NULL,typed_count INTEGER DEFAULT 0 NOT NULL,last_visit_time INTEGER NOT NULL,hidden INTEGER DEFAULT 0 NOT NULL);
                CREATE INDEX urls_url_index ON urls (url);
                ''',
                inputSchema={
                    "type": "object",
                    "required": ["sql_statement"],
                    "properties": {
                        "sql_statement": {
                            "type": "string",
                            "description": "SQL statement to execute",
                        }
                    },
                },
            ),
            types.Tool(
                name="fetch-visits-info-from-sqlite",
                description=
                '''
                Use SQL to query SQLite data tables that contain "visits" information of the selected browser history (Chrome, Brave, or Microsoft Edge). Table schema:
                CREATE TABLE visits(id INTEGER PRIMARY KEY AUTOINCREMENT,url INTEGER NOT NULL,visit_time INTEGER NOT NULL,from_visit INTEGER,transition INTEGER DEFAULT 0 NOT NULL,segment_id INTEGER,visit_duration INTEGER DEFAULT 0 NOT NULL,incremented_omnibox_typed_score BOOLEAN DEFAULT FALSE NOT NULL,opener_visit INTEGER,originator_cache_guid TEXT,originator_visit_id INTEGER,originator_from_visit INTEGER,originator_opener_visit INTEGER,is_known_to_sync BOOLEAN DEFAULT FALSE NOT NULL, consider_for_ntp_most_visited BOOLEAN DEFAULT FALSE NOT NULL, external_referrer_url TEXT, visited_link_id INTEGER, app_id TEXT);
                CREATE INDEX visits_url_index ON visits (url);
                CREATE INDEX visits_from_index ON visits (from_visit);
                CREATE INDEX visits_time_index ON visits (visit_time);
                CREATE INDEX visits_originator_id_index ON visits (originator_visit_id);
                ''',
                inputSchema={
                    "type": "object",
                    "required": ["sql_statement"],
                    "properties": {
                        "sql_statement": {
                            "type": "string",
                            "description": "SQL statement to execute",
                        }
                    },
                },
            ),
        ]

    if path is None:
        path = default_history_path(browser)
    else:
        path = Path(path)
    
    if not path.exists():
        raise click.ClickException(f"History file not found at {path}. Specify --path for another profile.")
    
    global history_file_original
    history_file_original = str(path)

    from mcp.server.stdio import stdio_server
    async def arun():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream, write_stream, app.create_initialization_options()
            )

    global history_file_tmp
    with tempfile.TemporaryDirectory(prefix=f"{browser}-history-") as snapshot_dir:
        history_file_tmp = str(Path(snapshot_dir) / "History")
        anyio.run(arun)

    return 0
