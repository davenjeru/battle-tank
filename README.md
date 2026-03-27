# Battle Tank Arena Bot

AI-powered tank bot for [Battle Tank Arena](https://battle-tank-arena.vercel.app).
Uses the OpenAI Agents SDK to connect to the arena's MCP server, register a tank,
and autonomously play each turn.

## Quick start

```bash
# Set your OpenAI API key
export OPENAI_API_KEY="sk-..."

# Optional: change tank name (default: Squad4)
export TANK_NAME="Squad4"

# Run the bot
uv run tank.py
```

## How it works

1. **Register** — connects to the MCP server and registers a tank.
2. **Poll** — watches the game state, waiting for the game to start and for our turn.
3. **Play** — when it's our turn, an LLM agent calls `get_valid_actions`, then
   rotates/moves/fires using both dice.
4. **Record** — every turn's actions and outcomes are saved to `games/` as JSON
   for strategy refinement in future games.

## MCP server — tools

Live endpoint: [`https://battle-tank-arena.vercel.app/api/mcp`](https://battle-tank-arena.vercel.app/api/mcp)  
Transport: Streamable HTTP (MCP). After registration, send header `x-player-token: <YourTankName>` on all later calls.

Every tool returns a normal MCP **tool result** whose text body is a **single JSON object** (parse the first text `content` item). Shapes below describe that JSON.

| Tool | Input (arguments) | Output (JSON body, summarized) |
| :--- | :--- | :--- |
| **`register`** | **`name`** *(string, required)* — tank display name; unique, 1–20 chars; becomes your auth token | **`id`**, **`name`**, **`message`**. Reconnects may include **`reconnected`**: `true` |
| **`get_valid_actions`** | *(no arguments)* | **`gridSize`**, your **`x`** / **`y`** / **`direction`** / **`score`**, dice values, **`usedDice`**, enemy positions, **`validRotations`** (compass directions you may face). For each unused die: **`validMoves`** and **`validShots`** (per direction, landing **`target`** is enemy **`name`** for a guaranteed hit, or **`null`** for a miss) |
| **`rotate`** | **`direction`** *(string, required)* — one of `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW` | Success: updated facing / state. If barrel tip would leave the grid: failure plus **`validRotations`** listing allowed directions |
| **`move`** | **`die`** *(integer, required)* — `1` or `2` (which die to spend) | Success: new position, consumed die. If blocked (path off grid, occupied destination, or barrel off grid): die **not** consumed; response includes **`validDirections`** you can move along |
| **`fire`** | **`die`** *(integer, required)* — `1` or `2` | **Hit**: enemy loses 1 **`score`** (0 = eliminated). **Miss**: empty cell. Die consumed on hit or miss. If shot would leave the grid: blocked, die **not** consumed; **`validFiringDirections`** shows legal shots and what each would hit |
| **`get_game_state`** | *(no arguments)* | **`status`**: `lobby` \| `running` \| **`ended`**. **`currentTurnId`**, **`turnOrder`**, **`tanks`**: map of tank id → **`name`**, **`x`**, **`y`**, **`direction`**, **`score`**, **`lastRoll`**, **`usedDice`**, **`turnPhase`**, etc. |

**Field notes**

- **Die indices** — `1` and `2` refer to your two dice for that turn (not the numeric face value).
- **60s turn limit** — if both dice are not used in time, the turn may be skipped.
- **Registration** — only while **`status`** is `lobby`; use **`get_game_state`** to know when the match is live.

## Deploy to Railway

1. Push this repo to GitHub.
2. Create a new project on [Railway](https://railway.app) and connect the repo.
3. Add environment variables: `OPENAI_API_KEY`, `TANK_NAME`.
4. Deploy — Railway auto-detects the Dockerfile.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key |
| `TANK_NAME` | No | `Squad4` | Tank name (max 20 chars, must be unique) |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model for tactical decisions |
| `AGENT_MAX_TURNS` | No | `25` | Max model/tool loop turns per arena turn (avoids “Max turns exceeded”) |
| `MCP_HTTP_TIMEOUT` | No | `90` | HTTP connect/send timeout (seconds) for Streamable MCP |
| `MCP_SSE_READ_TIMEOUT` | No | `600` | Max wait on SSE read (seconds) |
| `MCP_SESSION_TIMEOUT` | No | `90` | MCP `ClientSession` request timeout (seconds) |
