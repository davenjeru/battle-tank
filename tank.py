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

from history import GameRecorder, build_history_prompt

MCP_URL = "https://battle-tank-arena.vercel.app/api/mcp"
TANK_NAME = os.getenv("TANK_NAME", "Squad4")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
POLL_INTERVAL = 2  # seconds between game-state polls

SYSTEM_PROMPT = """\
You are an autonomous battle-tank AI competing in Battle Tank Arena (20×20 grid).
Your tank is named "{tank_name}".

## Rules
- Each turn you get 2 dice. You MUST use both.
- **rotate** (free, no die cost) — change facing to N/NE/E/SE/S/SW/W/NW.
- **move(die=1|2)** — advance exactly that die's value in your facing direction. Costs the die.
- **fire(die=1|2)** — shoot that die's value cells in your facing direction. Costs the die.
- A hit removes 1 HP from the target. Tanks start at 5 HP; 0 = eliminated.

## Your turn procedure
1. Call **get_valid_actions** FIRST. It tells you your dice values, position, enemies, and
   exactly which shots will hit (target != null) vs miss (target == null).
2. Look for any rotation where a fire action would HIT an enemy. Prioritise kills.
3. If no shot can hit, reposition: move toward the nearest enemy or toward the grid center.
4. After using die 1, re-evaluate — rotate again if needed, then use die 2.
5. ALWAYS consume both dice. Never end your turn with an unused die.

## Strategy
- Shots that hit are always better than moves. Take every hit you can.
- If both dice can hit (possibly after rotating), fire both.
- If only one can hit, fire that one and move with the other to improve next-turn position.
- Prefer the center of the grid — edges limit your rotation options.
- When no hits are available, close distance to the nearest enemy.

{history}
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
    history_context = build_history_prompt()
    prompt = SYSTEM_PROMPT.format(tank_name=TANK_NAME, history=history_context)

    print(f"[bot] Connecting as '{TANK_NAME}' to {MCP_URL}")

    async with MCPServerStreamableHttp(
        name="battle-tank-arena",
        params={
            "url": MCP_URL,
            "headers": {"x-player-token": TANK_NAME},
        },
        cache_tools_list=True,
    ) as server:
        # ── Phase 1: Register ──────────────────────────────────────
        print("[bot] Registering tank …")
        try:
            reg = await server.call_tool("register", {"name": TANK_NAME})
            print(f"[bot] Registration: {reg.content[0].text}")
        except Exception as e:
            msg = str(e)
            if "already" in msg.lower():
                print(f"[bot] Already registered — continuing.")
            else:
                print(f"[bot] Registration error: {msg}")
                print("[bot] Continuing anyway (may already be registered) …")

        # ── Phase 2: Wait for game start ───────────────────────────
        print("[bot] Waiting for game to start …")
        recorder = GameRecorder(TANK_NAME)
        players_recorded = False

        while True:
            try:
                state_result = await server.call_tool("get_game_state", {})
                state = _parse_tool_result(state_result)
            except Exception as e:
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
                    recorder.finish_game(result, tanks)
                    print(f"[bot] Result: {result}  |  Final scores: {recorder.final_scores}")
                else:
                    recorder.finish_game("eliminated", tanks)
                    print("[bot] We were eliminated.")
                break

            if status == "lobby":
                print("[bot] Still in lobby …", end="\r")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # status == "running"
            tanks = state.get("tanks", {})
            if not players_recorded:
                recorder.record_players(tanks)
                players_recorded = True

            match = _find_our_tank(tanks, TANK_NAME)
            if not match:
                print("[bot] Our tank is not in the game — eliminated?")
                recorder.finish_game("eliminated", tanks)
                break

            our_id, our_tank = match
            current_turn = state.get("currentTurnId")

            if current_turn != our_id:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # ── Phase 3: It's our turn — let the agent play ────────
            print(f"\n[bot] === OUR TURN (score={our_tank['score']}) ===")
            turn = recorder.start_turn()

            agent = Agent(
                name="TankAgent",
                instructions=prompt,
                mcp_servers=[server],
                model=MODEL,
                model_settings=ModelSettings(tool_choice="required"),
            )

            turn_msg = (
                f"It's your turn. You are '{TANK_NAME}'. "
                "Call get_valid_actions now, then make your moves. Use BOTH dice."
            )

            try:
                result = await Runner.run(agent, turn_msg)

                # Record actions from the run
                for item in result.new_items:
                    if hasattr(item, "raw_item") and hasattr(item.raw_item, "name"):
                        action_name = item.raw_item.name
                        action_args = getattr(item.raw_item, "arguments", "{}")
                        if isinstance(action_args, str):
                            try:
                                action_args = json.loads(action_args)
                            except json.JSONDecodeError:
                                action_args = {"raw": action_args}

                        action_output = ""
                        if hasattr(item, "output"):
                            action_output = item.output or ""

                        if action_name == "get_valid_actions":
                            try:
                                turn.record_valid_actions(json.loads(action_output))
                            except (json.JSONDecodeError, TypeError):
                                turn.record_valid_actions({"raw": str(action_output)})
                        else:
                            try:
                                res = json.loads(action_output)
                            except (json.JSONDecodeError, TypeError):
                                res = {"raw": str(action_output)}
                            turn.record_action(
                                action_name,
                                action_args if isinstance(action_args, dict) else {},
                                res,
                            )

                print(f"[bot] Agent finished: {result.final_output}")
            except Exception as e:
                print(f"[bot] Agent error: {e}")

            await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is required.")
        sys.exit(1)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
