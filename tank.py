"""Battle Tank Arena bot — register, poll, and play using the OpenAI Agents SDK."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp
from agents.model_settings import ModelSettings
from dotenv import load_dotenv

load_dotenv(override=True)

MCP_URL = "https://battle-tank-arena.vercel.app/api/mcp"
TANK_NAME = os.getenv("TANK_NAME", "Squad4")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "25"))
LOBBY_POLL_INTERVAL = 30  # seconds between polls while in lobby
POLL_INTERVAL = 2         # seconds between polls during gameplay

# MCP / HTTP timeouts (seconds) — Vercel cold starts + busy turns need headroom
MCP_HTTP_TIMEOUT = float(os.getenv("MCP_HTTP_TIMEOUT", "90"))
MCP_SSE_READ_TIMEOUT = float(os.getenv("MCP_SSE_READ_TIMEOUT", "600"))
MCP_SESSION_TIMEOUT = float(os.getenv("MCP_SESSION_TIMEOUT", "90"))

SYSTEM_PROMPT = """\
You are an autonomous battle-tank AI competing in Battle Tank Arena (20x20 grid).
Your tank is named "{tank_name}".

## Rules
- Each turn you get 2 dice. You MUST use both.
- **rotate** (free, no die cost) — change facing to N/NE/E/SE/S/SW/W/NW.
- **move(die=1|2)** — advance exactly that die's value in your facing direction. Costs the die.
- **fire(die=1|2)** — shoot that die's value cells in your facing direction. Costs the die.
- A hit removes 1 HP from the target. Tanks start at 5 HP; 0 = eliminated.

## Firing discipline (mandatory)
- Call **get_valid_actions** and treat its JSON as the **only** source of truth for hits.
- For each unused die, **validShots** lists directions (after you face that way) and what the shot
  would land on. A **guaranteed hit** is when the entry for that direction is a **non-null enemy
  tank name** (a string), not `null` and not missing.
- You may call **fire(die=N)** **only** if: you have already **rotate**d so your facing direction
  matches a direction where, in the **latest** get_valid_actions response for die N, that shot's
  target is a **non-null enemy name**. Do **not** fire if the only available targets are `null`
  (miss) when you could **move** that die instead.
- If an unused die has **no** direction with a non-null hit in get_valid_actions, use **move**
  for that die (after **rotate** to a direction listed in **validMoves** for that die), not fire.
- If and only if **move** is impossible for an unused die and every legal shot for that die is a
  miss (`null`), you may fire to spend the die (last resort).

## Your turn procedure
1. Call **get_valid_actions** first.
2. Plan both dice: prefer **fire** only where the JSON shows a non-null target; otherwise **move**.
3. After spending one die, call **get_valid_actions** again before spending the second (dice /
   board state change).
4. ALWAYS consume both dice. Never end your turn with an unused die.

## Strategy
- Prioritize lowest-HP enemies when choosing among guaranteed hits.
- If both dice can hit (after rotates), fire both.
- If only one can hit, fire that one and **move** with the other when no second hit exists.
- Prefer positions toward the grid center when moving.

"""


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract the JSON dict from a CallToolResult."""
    text = result.content[0].text
    return json.loads(text)


def _find_our_tank(tanks: dict[str, Any], name: str) -> tuple[str, dict[str, Any]] | None:
    for tid, t in tanks.items():
        if t["name"] == name:
            return tid, t
    return None


async def run_bot() -> None:
    prompt = SYSTEM_PROMPT.format(tank_name=TANK_NAME)

    print(f"[bot] Connecting as '{TANK_NAME}' to {MCP_URL}")

    async with MCPServerStreamableHttp(
        name="battle-tank-arena",
        params={
            "url": MCP_URL,
            "headers": {"x-player-token": TANK_NAME},
            "timeout": MCP_HTTP_TIMEOUT,
            "sse_read_timeout": MCP_SSE_READ_TIMEOUT,
        },
        client_session_timeout_seconds=MCP_SESSION_TIMEOUT,
        cache_tools_list=True,
        max_retry_attempts=3,
    ) as server:
        # ── Phase 1: Register ──────────────────────────────────────
        print("[bot] Registering tank …")
        try:
            reg = await server.call_tool("register", {"name": TANK_NAME})
            txt = reg.content[0].text
            print(f"[bot] Registration: {txt}")
        except Exception as e:
            msg = str(e)
            if "already" in msg.lower():
                print(f"[bot] Already registered — continuing.")
            else:
                print(f"[bot] Registration error: {msg}")
                print("[bot] Continuing anyway (may already be registered) …")

        # ── Phase 2: Wait for game start ───────────────────────────
        print("[bot] Waiting for game to start …")
        players_recorded = False

        while True:
            try:
                state_result = await server.call_tool("get_game_state", {})
                state = _parse_tool_result(state_result)
            except BaseException as e:
                print(f"[bot] Error polling state: {e}")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            status = state.get("status", "unknown")

            if status == "ended":
                print("[bot] Game has ended.")
                tanks = state.get("tanks", {})
                match = _find_our_tank(tanks, TANK_NAME)
                if match:
                    _, our = match
                    best_score = max((t["score"] for t in tanks.values()), default=0)
                    result = "win" if our["score"] == best_score else "loss"
                    if our["score"] <= 0:
                        result = "eliminated"
                    print(f"[bot] Result: {result}")
                else:
                    print("[bot] We were eliminated.")
                break

            if status == "lobby":
                print("[bot] Still in lobby …", end="\r")
                await asyncio.sleep(LOBBY_POLL_INTERVAL)
                continue

            # status == "running"
            tanks = state.get("tanks", {})
            if not players_recorded:
                players_recorded = True

            match = _find_our_tank(tanks, TANK_NAME)
            if not match:
                print("[bot] Our tank is not in the game — eliminated?")
                break

            our_id, our_tank = match
            current_turn = state.get("currentTurnId")

            if current_turn != our_id:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # ── Phase 3: It's our turn — let the agent play ────────
            print(f"\n[bot] === OUR TURN (score={our_tank['score']}) ===")

            agent = Agent(
                name="TankAgent",
                instructions=prompt,
                mcp_servers=[server],
                model=MODEL,
                model_settings=ModelSettings(tool_choice="required"),
            )

            turn_msg = (
                f"It's your turn. You are '{TANK_NAME}'. "
                "Call get_valid_actions now, then make your moves. Use BOTH dice. "
                "Follow firing discipline: fire only when that die+direction shows a non-null enemy target in get_valid_actions; otherwise move."
            )

            try:
                result = await Runner.run(agent, turn_msg, max_turns=AGENT_MAX_TURNS)
                print(f"[bot] Agent finished: {result.final_output}")
            except asyncio.CancelledError:
                # MCP/tool stack timed out or closed the anyio cancel scope — don’t kill the bot
                print(
                    "[bot] Agent run cancelled (usually MCP slow/timeout). "
                    "Skipping turn recording; will poll again …"
                )
            except TimeoutError as e:
                print(f"[bot] Agent timeout: {e}")
            except Exception as e:
                print(f"[bot] Agent error: {type(e).__name__}: {e}")

            await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is required.")
        sys.exit(1)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
