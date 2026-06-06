"""Task 1 strategy scaffold for the duck_curve scenario.

This file intentionally defines the controller shape and state-machine
plumbing only. Dispatch logic can be added once we decide the approach.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Mode(str, Enum):
    OBSERVE = "observe"
    DISPATCH = "dispatch"


class ControllerMemory:
    def __init__(self) -> None:
        self.mode: Mode = Mode.OBSERVE
        self.last_time: int | None = None
        self.last_soc: float | None = None
        self.last_net_grid_power_mw: float | None = None


class Strategy:
    """Boilerplate Strategy lifecycle accepted by the playtest runner."""

    def __init__(self) -> None:
        self.memory = ControllerMemory()

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        self._observe(state)
        return {"agent_plan": {"task": "duck_curve"}}

    def replan(self, state: dict[str, Any], alerts: list[dict[str, Any]]) -> dict[str, Any]:
        self._observe(state)
        return {"agent_plan": {"task": "duck_curve", "alerts_seen": len(alerts)}}

    def step(self, state: dict[str, Any]) -> dict[str, float]:
        self._observe(state)
        self.memory.mode = Mode.DISPATCH
        return self._empty_action()

    def _observe(self, state: dict[str, Any]) -> None:
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))

        self.memory.last_time = int(state.get("time", 0))
        self.memory.last_soc = float(state.get("soc", 0.0))
        self.memory.last_net_grid_power_mw = demand - solar

    @staticmethod
    def _empty_action() -> dict[str, float]:
        return {
            "battery_flow_mw": 0.0,
            "emergency_generator": 0.0,
            "curtail_solar": 0.0,
            "fcas_reserve_mw": 0.0,
        }


_DEFAULT_STRATEGY = Strategy()


def controller(state: dict[str, Any]) -> dict[str, float]:
    """Function-style entry point for tools that do not use Strategy classes."""
    return _DEFAULT_STRATEGY.step(state)


if __name__ == "__main__":
    from watt_the_hack.playtest import run_playtest

    result = run_playtest(__file__, "duck_curve", plots=True, open_report=False)
    print(f"\nRaw cost (lower wins): ${result['metrics']['final_score']:,.2f}")
