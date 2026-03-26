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
