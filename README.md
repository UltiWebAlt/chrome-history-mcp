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


