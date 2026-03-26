from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GAMES_DIR = Path(__file__).parent / "games"


class TurnRecord:
    def __init__(self, turn_number: int):
        self.turn_number = turn_number
        self.valid_actions: dict[str, Any] | None = None
        self.actions: list[dict[str, Any]] = []

    def record_valid_actions(self, data: dict[str, Any]) -> None:
        self.valid_actions = data

    def record_action(self, action: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        self.actions.append({"action": action, "args": args, "result": result})

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_number": self.turn_number,
            "valid_actions": self.valid_actions,
            "actions": self.actions,
        }


class GameRecorder:
    """Records every turn of a single game session to a JSON file."""

    def __init__(self, tank_name: str):
        self.tank_name = tank_name
        self.game_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        self.players: list[dict[str, Any]] = []
        self.turns: list[TurnRecord] = []
        self.result: str | None = None
        self.final_scores: dict[str, int] = {}
        self._current_turn: TurnRecord | None = None

    def record_players(self, tanks: dict[str, Any]) -> None:
        self.players = [
            {"name": t["name"], "x": t["x"], "y": t["y"], "direction": t["direction"]}
            for t in tanks.values()
        ]

    def start_turn(self) -> TurnRecord:
        turn = TurnRecord(len(self.turns) + 1)
        self._current_turn = turn
        self.turns.append(turn)
        return turn

    @property
    def current_turn(self) -> TurnRecord | None:
        return self._current_turn

    def finish_game(self, result: str, tanks: dict[str, Any]) -> None:
        self.result = result
        self.final_scores = {t["name"]: t["score"] for t in tanks.values()}
        self._save()

    def _save(self) -> None:
        GAMES_DIR.mkdir(exist_ok=True)
        path = GAMES_DIR / f"{self.game_id}.json"
        data = {
            "game_id": self.game_id,
            "tank_name": self.tank_name,
            "players": self.players,
            "total_turns": len(self.turns),
            "turns": [t.to_dict() for t in self.turns],
            "result": self.result,
            "final_scores": self.final_scores,
        }
        path.write_text(json.dumps(data, indent=2))

    def __repr__(self) -> str:
        return f"<GameRecorder {self.game_id} tank={self.tank_name} turns={len(self.turns)}>"


def load_past_games(limit: int = 5) -> list[dict[str, Any]]:
    """Load the most recent *limit* game logs, newest first."""
    if not GAMES_DIR.exists():
        return []
    files = sorted(GAMES_DIR.glob("*.json"), reverse=True)[:limit]
    games: list[dict[str, Any]] = []
    for f in files:
        try:
            games.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return games


def build_history_prompt(limit: int = 5) -> str:
    """Produce a concise strategy summary from past games for the agent prompt."""
    games = load_past_games(limit)
    if not games:
        return ""

    wins = sum(1 for g in games if g.get("result") == "win")
    losses = sum(1 for g in games if g.get("result") == "loss")
    eliminations = sum(1 for g in games if g.get("result") == "eliminated")

    hit_dirs: dict[str, list[bool]] = {}
    for game in games:
        for turn in game.get("turns", []):
            va = turn.get("valid_actions") or {}
            direction = va.get("direction", "?")
            for act in turn.get("actions", []):
                if act["action"] == "fire":
                    hit = "hit" in str(act.get("result", "")).lower()
                    hit_dirs.setdefault(direction, []).append(hit)

    best_dirs: list[str] = []
    for d, hits in sorted(hit_dirs.items(), key=lambda kv: -sum(kv[1])):
        total = len(hits)
        hit_count = sum(hits)
        if total >= 2:
            best_dirs.append(f"{d}: {hit_count}/{total} hits")

    lines = [
        f"## Past game history ({len(games)} games)",
        f"Record: {wins}W / {losses}L / {eliminations}E",
    ]
    if best_dirs:
        lines.append("Firing accuracy by facing direction: " + ", ".join(best_dirs[:4]))

    for i, g in enumerate(games[:3]):
        scores = g.get("final_scores", {})
        lines.append(
            f"Game {i + 1}: result={g.get('result')}, "
            f"turns={g.get('total_turns')}, scores={scores}"
        )

    return "\n".join(lines)
