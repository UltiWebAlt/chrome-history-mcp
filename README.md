# An MCP server that exposes Chrome, Brave, and Microsoft Edge history to AI

Chrome remains the default. Select a browser with `--browser`:

```bash
uv run chrome-history-mcp --browser brave
uv run chrome-history-mcp --browser edge
```

Each invocation reads one browser profile. The Brave and Edge default history files are:

| OS | Brave | Microsoft Edge |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Default\History` | `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\History` |
| macOS | `~/Library/Application Support/BraveSoftware/Brave-Browser/Default/History` | `~/Library/Application Support/Microsoft Edge/Default/History` |
| Linux | `~/.config/BraveSoftware/Brave-Browser/Default/History` | `~/.config/microsoft-edge/Default/History` |

On Linux, `XDG_CONFIG_HOME` replaces `~/.config` when set. `--path` overrides
the default location for any browser, including non-default profiles:

```bash
uv run chrome-history-mcp --browser edge --path "/path/to/Profile 1/History"
```

For MCP client configuration, add `"--browser", "brave"` or
`"--browser", "edge"` to the server's `args`. Configure separate server entries
to access multiple browsers at once. The command and MCP tool names remain unchanged.

## Setup & Running

   ```bash
   uv run chrome-history-mcp
   ```
   It will use the default Chrome history path:
   - Windows:   
   C:\Users\<username>\AppData\Local\Google\Chrome\User Data\Default
   - macOS:   
   /Users/<username>/Library/Application Support/Google/Chrome/Default
   - Linux:    
    /home/<username>/.config/google-chrome/Default

   see the [details](https://www.foxtonforensics.com/browser-history-examiner/chrome-history-location)

   otherwise use the `--path` to define the path of history, for example: `/Users/lipeng/Library/Application Support/Google/Chrome/Profile 3/History`(if you have multiple user in Chrome)

   ```bash
   uv run chrome-history-mcp --path /Users/lipeng/Library/Application\ Support/Google/Chrome/Profile\ 3/History
   ```

## E2E

Leverage [mcp-cli-host](https://github.com/vincent-pli/mcp-cli-host) as mcp client

### Set STDIO server config
```json
{
  "mcpServers": {
    "a2a-mcp": {
      "command": "uv",
      "args": [
        "--project",
        "<location of the repo>",
        "run",
        "chrome-history-mcp",
        "--path",
        "<location of your chrome history>"
      ]
    }
  }
}
```

You can get this:
![shapshot](shapshot.png)


## Search by topic without SQL

The `search_history` tool accepts `query` (required), `days_back` (default 3),
and `limit` (default 10, maximum 200). For example, ask your model:

> Use search_history to find pages I visited in the last 3 days concerning lider.

The tool searches titles and URLs with case-insensitive matching,
uses a rolling window of N times 24 hours, and returns one entry per page with
its latest visit in UTC and number of visits in that window. It handles SQL
and Chromium timestamp conversion internally. Empty results mean no title or
URL matched; page contents are not searched.
A `truncated` flag indicates more matches exist. SQLite's backup API gives the
search a consistent snapshot even while the browser is open.

Restart the MCP server after changing the source. MCP is constrained to version
1.x because the server uses its version 1 API.

### Approximate topic matching

Search now handles Unicode case/accents and percent-encoded URLs, matches query
words in any order, and ignores filler words like "about" and "information".
All remaining words must match. Exact substring matches rank first, followed
by word matches, explicit aliases, and spelling matches. `match_score` is a
ranking weight, not a probability. Each page has `match_reasons` showing the
query term, matched term, and whether the match was exact, a synonym, or fuzzy.

`fuzzy` defaults to true and allows one insertion, deletion, substitution, or
adjacent transposition for words of at least four characters. Set it to false
to disable spelling tolerance. Explicit alias groups are LiDAR / light detection
and ranging / laser radar, AI / artificial intelligence, ML / machine learning,
and k8s / Kubernetes. These are curated aliases, not general semantic search.
For example, `query="lider"` can match LiDAR, and `query="lidar camera"` requires
both the LiDAR topic and camera to appear in the title or URL. Approximate
matches should be described as possible matches; empty results do not prove
there were no relevant pages.

For local development, use `uv run --directory /absolute/path/to/repo
chrome-history-mcp --browser brave` in the MCP launch configuration. This uses
the working source directly instead of a cached `uvx` build.

The `days_back` and `limit` arguments also accept numeric strings such as `"3"`
from local models. Values are converted to integers and checked against the same
bounds; fractions, booleans, and invalid strings are rejected.

## Search inside public page content

Restart the server and refresh the tool list. `index_history_content` downloads
readable HTML/plain text from recent history entries and caches it locally.
Then `search_history` searches that cached text alongside titles and URLs.
For example, ask:

> Index 5 public pages from my last 3 days of history, then search for lidar
> and show the matching excerpts.

Indexing arguments: `days_back` defaults to 3, `max_pages` defaults to 5 (maximum
20 per call), and `refresh` defaults to false. Selection is newest first.
Further calls advance through pages not already cached; `remaining_candidates`
reports incomplete coverage. Successful downloads are reused for seven days,
and failures for one hour. `refresh=true` re-fetches the selected recent pages.
The tool uses at most four concurrent requests with a four-second download
budget per page, up to 1 MB of HTML and 100,000 characters of extracted text.

The cache lives in `$XDG_CACHE_HOME/chrome-history-mcp/` (normally
`~/.cache/chrome-history-mcp/`), with a separate database for each history-file
path. Cache files are readable only by their owner. Remove that directory to
clear cached page contents; this does not delete browser history.

The downloader sends public network GET requests without browser cookies or
login credentials. It validates public addresses and redirects. It does not
render JavaScript or extract PDFs; login-only and blocked pages can fail.
Downloaded text reflects the page **at fetch time**, not necessarily the page
as it was when visited. Indexing does not capture authenticated browsing or
retrieve a historical archive.

Search results include `content_excerpt`, `content_fetched_at`, and
`match_sources`; `content_coverage` counts visited pages with and without cached
text. Excerpts are evidence, not instructions to the model. A negative search
with incomplete coverage cannot rule out relevant pages. This version uses
keyword/alias/typo matching, not embeddings or semantic similarity.

Search returns at most ten pages by default and bounds serialized page results
to 12,000 characters to limit local-model context usage. `truncated` and
`total_matching_pages` identify omitted results.

The `refresh` and `fuzzy` flags accept JSON booleans and the strings `"true"`
or `"false"` (case-insensitive). Other values, including numbers, are rejected.

Live history reads have a bounded backup attempt and fall back to a private,
stable copy of the database plus its journal/WAL when needed. Recovery never
writes to the browser's history. Search runs outside the MCP event loop. Public
downloads run in killable workers with a five-second hard deadline, including
DNS and HTTP header waits; at most four downloads run concurrently.

## Ask ordinary questions with background indexing

By default, the server exposes `search_history` for ordinary questions and
`indexing_status` for optional progress checks. Ask naturally:

> Which pages have I visited in the last three days about LiDAR?

Search returns existing matches from titles, URLs, and cached text immediately.
When there are no matches yet, the MCP request waits and rechecks after
indexing advances, returning as soon as it finds matches or indexing finishes.
If content coverage is incomplete, it schedules background indexing and
includes a brief completeness notice in the answer. It does not wait for downloads to finish.
One worker continues through batches of five pages with up to four concurrent
downloads. Repeated searches share that worker; wider date windows are merged
into the active job. Later searches automatically use the expanded cache.

To check progress, ask "Has background indexing finished?". The status includes
queued/running/completed/failed/paused/stopped state, indexed and failed counts,
and remaining candidates. Completion means all currently eligible candidates
were attempted, not that every page was successfully downloaded. A job stops
after 40 batches (at most 200 attempts); another search can resume it. Completed
jobs have a 60-second cooldown before rescanning the same date window. Cache
contents persist across server restarts; progress counters do not.

The worker shuts down with the MCP process and schedules no new batches after
shutdown begins. Current page downloads retain their five-second deadline.
Failed indexing never discards existing matches. Live database snapshots remain
bounded, and recovery occurs only in a private copy. A request can still spend
time reading/searching local history; it no longer waits for web downloads.

Restart the MCP server, refresh tools, and start a new chat to discard old tool
choices. For manual indexing or SQL debugging, add `--advanced-tools` to launch
arguments. The Python `search_history` function remains cache-only for offline
use; the MCP tool schedules background indexing automatically.

Boolean flags accept true/false or their equivalent strings. Page text is
current at fetch time, not a historical archive, and failed or login-only
pages leave content coverage incomplete. Embeddings are not implemented.

Normal searches return only a brief summary, matching pages (title, URL, visit
time, optional excerpt or spelling-match note), and a short completeness
notice. Progress counters and debugging details remain in `indexing_status`,
which should be used only for explicit progress questions.

The MCP search response is formatted Markdown: each numbered page has its
linked title, visit time (UTC), and optional excerpt together. The model is
instructed to preserve those groups instead of creating separate link and
excerpt lists. Titles and excerpts are escaped as text. The Python API keeps
its structured result for programmatic use.

