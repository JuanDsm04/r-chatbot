# chatbot-redes

A console chatbot that acts as an **MCP host**: it connects to an LLM through the
Anthropic API, connects to several **MCP (Model Context Protocol)** servers at the
same time, and lets the model use the tools those servers expose.

## Implemented features

| # | Project requirement |
|---|---------------------|
| 1 | Connect to an LLM at the API level |
| 2 | Keep conversation context within a session |
| 3 | Keep and display a log of every MCP request/response |
| 4 | Use the official Filesystem and Git MCP servers |
| 5 | Use an own local MCP server |
| 6 | Use MCP servers written by other students |
| 7 | Use an own remote MCP server |

Adding any MCP server — official, from a classmate,
local or remote — only requires one entry in `config/mcp_servers.json`.

## Requirements

* Python **3.10+**
* An Anthropic API key ([console.anthropic.com](https://console.anthropic.com/))
* **Node.js 18+** (for `npx`), needed only by the official Filesystem MCP server,
  which is distributed as an npm package
* Git installed, with `user.name` and `user.email` configured. The Git MCP server
  itself is installed by `requirements.txt`, so `uv`/`uvx` is not needed.

## Installation

```bash
git clone https://github.com/JuanDsm04/r-chatbot.git
cd chatbot-redes

python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Create the environment file and the demo working directory:

```bash
cp .env.example .env      # Windows PowerShell: copy .env.example .env
# open .env and set ANTHROPIC_API_KEY

mkdir sandbox
git init sandbox
```

### Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | yes | — | Key used for every API call |
| `CHATBOT_MODEL` | no | `claude-sonnet-5` | Model id |
| `CHATBOT_MAX_TOKENS` | no | `2048` | Max tokens per reply |
| `CHATBOT_MCP_CONFIG` | no | `config/mcp_servers.json` | MCP server registry |
| `CHATBOT_LOG_DIR` | no | `logs` | Where the MCP log is written |
| `CHATBOT_ECHO_MCP_LOG` | no | `0` | `1` prints every MCP exchange live |

## Usage

```bash
python -m src.main  

# Optional flags: `--model claude-opus-5`, `--config config/other_registry.json`.
```




Example in console:
```
==========================================================
                     CHATBOT REDES
==========================================================
Modelo: claude-sonnet-5
Conectando a 2 servidor(es) MCP...
  [ok]   filesystem (stdio) v0.2.0 - 14 tools - Filesystem MCP server oficial
  [ok]   git (stdio) v1.29.1 - 12 tools - Git MCP server oficial


Herramientas disponibles: 26. Escribe /help para los comandos.


tu> crea un archivo README.md en el sandbox y haz commit
  [tool] -> filesystem__write_file({"path": ".../sandbox/README.md", ...})
  [tool] <- Successfully wrote to .../sandbox/README.md
  [tool] -> git__git_add({"repo_path": ".../sandbox", "files": ["README.md"]})
  [tool] <- Files staged successfully
  [tool] -> git__git_commit({"repo_path": ".../sandbox", "message": "Add README"})
  [tool] <- Changes committed successfully with hash 55a3cda...


bot> Listo. Cree README.md, lo agregue al indice y lo commitee (55a3cda).
```

### Console commands

| Command | What it does |
|---------|--------------|
| `/help` | Show the command list |
| `/servers` | Connected servers, transports, versions, tool counts, failures |
| `/tools` | Every discovered tool, grouped by server |
| `/log [n\|all] [server]` | Show the MCP interaction log (last 20 by default) |
| `/stats` | Message counts per server and direction |
| `/usage` | Tokens consumed so far |
| `/clear` | Reset the conversation context |
| `/exit`, `/quit` | Close the chatbot |

## Configuring MCP servers

`config/mcp_servers.json` decides which servers the host connects to. Each entry
becomes one MCP client. `${PROJECT_ROOT}` expands to the repository root.

Local server (stdio — the host launches it as a subprocess):

```json
"filesystem": {
  "enabled": true,
  "transport": "stdio",
  "description": "Filesystem MCP server oficial",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}/sandbox"],
  "env": {}
}
```

Remote server (Streamable HTTP):

```json
"remote-demo": {
  "enabled": true,
  "transport": "http",
  "description": "Servidor MCP propio en la nube",
  "url": "https://your-service.run.app/mcp",
  "headers": { "Authorization": "Bearer ${REMOTE_MCP_TOKEN}" }
}
```

Set `"enabled": false` to keep an entry without connecting to it.

To check a server without spending API credits:

```bash
python scripts/check_servers.py
```

This connects to every enabled server, runs `initialize` and `tools/list`, and
prints what it found plus the interaction log. No API key needed.


## Project structure

```
chatbot-redes/
├── config/mcp_servers.json     MCP server registry
├── scripts/check_servers.py    Connectivity check (no API key needed)
├── src/
│   ├── config.py               .env + registry loading
│   ├── interaction_logger.py   JSONL + in-memory MCP log
│   ├── llm_client.py           Anthropic API client
│   ├── mcp_manager.py          One MCP client per server; discovery and routing
│   ├── session.py              Conversation context + tool-use loop
│   └── main.py                 Console REPL
├── logs/                       mcp_interactions.jsonl (git-ignored)
└── sandbox/                    Working directory for the demos (git-ignored)
```

## Demo scenarios

Start from a clean state:

```bash
rm -f logs/mcp_interactions.jsonl
python -m src.main
```

**Requirement 1: LLM at the API level.** Ask something answerable from training
data, then show `/stats` to prove no MCP traffic was produced:

```
tu> ¿Qué es JSON-RPC y en qué capa del modelo OSI opera?
tu> /stats
```

**Requirement 2: conversation context.** The second question only works if the
history is kept; `/clear` then breaks it on purpose, and that contrast is the
demonstration:

```
tu> ¿Quién fue Alan Turing?
tu> ¿En qué fecha nació?
tu> /clear
tu> ¿En qué fecha nació?
```

**Requirement 3: the MCP log.**

```
tu> /log 10
tu> /log all
tu> /log 20 git
tu> /stats
```

**Requirement 4: Filesystem + Git.** Type these one at a time so each tool call
is visible:

```
tu> ¿Qué archivos hay en el sandbox?
tu> Crea un archivo README.md en el sandbox que describa este proyecto en tres líneas.
tu> Muéstrame el estado del repositorio git del sandbox.
tu> Agrega el README al índice y haz un commit con el mensaje "Add project README".
tu> Muéstrame el historial de commits.
```

Verify outside the chatbot with `git -C sandbox log --oneline`.

A single question that uses both servers at once is the clearest demonstration
that the host coordinates multiple clients:

```
tu> Lee el README.md del sandbox, agrégale una sección "## Estado" que diga
    "en desarrollo", y haz commit del cambio.
```

Error handling is also worth showing — the Filesystem server is sandboxed to the
directory in its arguments, so this is refused, logged, and explained by the
model instead of crashing:

```
tu> Lee el archivo /etc/passwd
```

> **Note on "create a repository":** the official Git MCP server does not expose a
> `git_init` tool. It works on a repository that already exists and is passed with
> `--repository`, which is why setup runs `git init sandbox` once. Everything else
> in the scenario is done by the chatbot through the servers.

**Requirement 5: own local server.** Install it into the same venv and enable it:

```bash
pip install -e ../mcp-finanzas-pyme
```

```json
"finanzas-pyme": { "enabled": true, ... }
```

```
tu> ¿Cómo va mi negocio?
tu> ¿Cuánto gasté en agosto y en qué se me fue el dinero?
tu> Tengo Q45,000 en el banco. ¿Me alcanza para los próximos 5 meses?
tu> Marzo se sintió mal, ¿qué pasó?
```

**Requirements 6 and 7: more servers.** Add the entries, restart, and check with
`/servers` and `/tools`. Nothing else changes.

## References

* [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)
* [MCP specification](https://modelcontextprotocol.io/specification/2025-06-18)
* [MCP reference servers](https://github.com/modelcontextprotocol/servers)
* [JSON-RPC 2.0](https://www.jsonrpc.org/)