"""Task 1 strategy scaffold for the duck_curve scenario.

This file defines the controller shape, state-machine plumbing, and local
objective/cost functions. Dispatch logic can be added once we decide how to
search candidate actions.
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
        self.last_objective_terms: dict[str, float] = {}
        self.last_objective_score: float = 0.0


class Strategy:
    """Boilerplate Strategy lifecycle accepted by the playtest runner."""

    DT_HOURS = 0.25
    BATTERY_CAPACITY_MWH = 100.0
    MAX_INVERTER_MW = 50.0
    GRID_MAX_IMPORT_MW = 120.0
    GRID_MAX_EXPORT_MW = 50.0
    CHARGE_EFFICIENCY = 0.95
    DISCHARGE_EFFICIENCY = 0.95
    MAX_DIESEL_MW = 50.0

    EXPORT_TARIFF_PER_MWH = 50.0
    DIESEL_COST_PER_MWH = 1000.0
    BLACKOUT_PENALTY_PER_MWH = 100000.0
    OVERVOLTAGE_PENALTY_PER_MWH = 5000.0
    BATTERY_WEAR_PER_MWH = 50.0
    DEMAND_CHARGE_PER_MW = 1000.0
    CARBON_PRICE_PER_KG = 50.0
    DEFAULT_GRID_CO2_KG_PER_MWH = 0.7
    DIESEL_CO2_KG_PER_MWH = 0.27
    DUCK_CURVE_RAMP_CHARGE_PER_MW2 = 0.01

    OBJECTIVE_WEIGHTS = {
        "blackout_penalty": 1.0,
        "overvoltage_penalty": 1.0,
        "demand_charge": 1.0,
        "tariff_import": 1.0,
        "tariff_export": 1.0,
        "generator_fuel": 1.0,
        "battery_wear": 1.0,
        "carbon_cost": 1.0,
        "ramp_charge": 1.0,
        "soc_reserve_penalty": 1.0,
    }

    def __init__(self) -> None:
        self.memory = ControllerMemory()

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        self._observe(state)
        return {"agent_plan": {"task": "duck_curve"}}

    def replan(
        self, state: dict[str, Any], alerts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self._observe(state)
        return {"agent_plan": {"task": "duck_curve", "alerts_seen": len(alerts)}}

    def step(self, state: dict[str, Any]) -> dict[str, float]:
        self._observe(state)
        self.memory.mode = Mode.DISPATCH
        action = self._empty_action()
        self.memory.last_objective_terms = self.objective_terms(state, action)
        self.memory.last_objective_score = self.objective_score(state, action)
        return action

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

    def objective_score(
        self,
        state: dict[str, Any],
        action: dict[str, float],
        weights: dict[str, float] | None = None,
    ) -> float:
        terms = self.objective_terms(state, action)
        active_weights = self.OBJECTIVE_WEIGHTS if weights is None else weights
        return sum(active_weights.get(name, 1.0) * value for name, value in terms.items())

    def objective_terms(
        self, state: dict[str, Any], action: dict[str, float]
    ) -> dict[str, float]:
        physics = self.estimate_physics(state, action)
        dt = self.DT_HOURS

        import_mwh = physics["import_mw"] * dt
        export_mwh = physics["export_mw"] * dt
        diesel_mwh = physics["diesel_mw"] * dt
        battery_mwh = abs(physics["battery_mw"]) * dt

        grid_co2 = float(
            state.get("grid_co2_intensity", self.DEFAULT_GRID_CO2_KG_PER_MWH)
        )
        co2_kg = import_mwh * grid_co2 + diesel_mwh * self.DIESEL_CO2_KG_PER_MWH

        terms = {
            "blackout_penalty": (
                physics["unmet_demand_mw"] * dt * self.BLACKOUT_PENALTY_PER_MWH
            ),
            "overvoltage_penalty": (
                physics["overvoltage_mw"] * dt * self.OVERVOLTAGE_PENALTY_PER_MWH
            ),
            "demand_charge": physics["new_peak_import_delta_mw"]
            * self.DEMAND_CHARGE_PER_MW,
            "tariff_import": import_mwh * float(state.get("price", 0.0)),
            "tariff_export": -export_mwh * self.EXPORT_TARIFF_PER_MWH,
            "generator_fuel": diesel_mwh * self.DIESEL_COST_PER_MWH,
            "battery_wear": battery_mwh * self.BATTERY_WEAR_PER_MWH,
            "carbon_cost": co2_kg * self.CARBON_PRICE_PER_KG,
            "ramp_charge": self.ramp_charge(state, physics["net_grid_power_mw"]),
            "soc_reserve_penalty": self.soc_reserve_penalty(state, physics),
        }
        return {name: float(value) for name, value in terms.items()}

    def estimate_physics(
        self, state: dict[str, Any], action: dict[str, float]
    ) -> dict[str, float]:
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        soc = self._clip(float(state.get("soc", 0.0)), 0.0, 1.0)

        requested_battery_mw = float(action.get("battery_flow_mw", 0.0))
        battery_mw = self.feasible_battery_power(requested_battery_mw, soc)
        next_soc = self.next_soc(soc, battery_mw)

        diesel_mw = self._clip(
            float(action.get("emergency_generator", 0.0)), 0.0, self.MAX_DIESEL_MW
        )
        curtail_solar_mw = self._clip(
            float(action.get("curtail_solar", 0.0)), 0.0, solar
        )

        actual_solar_mw = solar - curtail_solar_mw
        raw_net_grid_power_mw = demand - actual_solar_mw - battery_mw - diesel_mw

        unmet_demand_mw = max(0.0, raw_net_grid_power_mw - self.GRID_MAX_IMPORT_MW)
        overvoltage_mw = max(
            0.0, -raw_net_grid_power_mw - self.GRID_MAX_EXPORT_MW
        )
        net_grid_power_mw = self._clip(
            raw_net_grid_power_mw,
            -self.GRID_MAX_EXPORT_MW,
            self.GRID_MAX_IMPORT_MW,
        )

        import_mw = max(0.0, net_grid_power_mw)
        export_mw = max(0.0, -net_grid_power_mw)
        peak_import_mw = float(state.get("peak_import_mw", 0.0))

        return {
            "battery_mw": battery_mw,
            "diesel_mw": diesel_mw,
            "curtail_solar_mw": curtail_solar_mw,
            "next_soc": next_soc,
            "raw_net_grid_power_mw": raw_net_grid_power_mw,
            "net_grid_power_mw": net_grid_power_mw,
            "import_mw": import_mw,
            "export_mw": export_mw,
            "unmet_demand_mw": unmet_demand_mw,
            "overvoltage_mw": overvoltage_mw,
            "new_peak_import_delta_mw": max(0.0, import_mw - peak_import_mw),
        }

    def feasible_battery_power(self, requested_mw: float, soc: float) -> float:
        clipped_mw = self._clip(
            requested_mw, -self.MAX_INVERTER_MW, self.MAX_INVERTER_MW
        )

        if clipped_mw > 0.0:
            max_discharge_mw = (
                soc * self.BATTERY_CAPACITY_MWH * self.DISCHARGE_EFFICIENCY
            ) / self.DT_HOURS
            return min(clipped_mw, max_discharge_mw)

        if clipped_mw < 0.0:
            headroom_mwh = (1.0 - soc) * self.BATTERY_CAPACITY_MWH
            max_charge_mw = headroom_mwh / (self.CHARGE_EFFICIENCY * self.DT_HOURS)
            return max(clipped_mw, -max_charge_mw)

        return 0.0

    def next_soc(self, soc: float, battery_mw: float) -> float:
        if battery_mw > 0.0:
            next_soc = soc - (battery_mw * self.DT_HOURS) / (
                self.BATTERY_CAPACITY_MWH * self.DISCHARGE_EFFICIENCY
            )
        elif battery_mw < 0.0:
            next_soc = soc - (
                battery_mw * self.CHARGE_EFFICIENCY * self.DT_HOURS
            ) / self.BATTERY_CAPACITY_MWH
        else:
            next_soc = soc

        return self._clip(next_soc, 0.0, 1.0)

    def ramp_charge(self, state: dict[str, Any], net_grid_power_mw: float) -> float:
        prev_grid = state.get("prev_grid_power_mw")
        if prev_grid is None:
            return 0.0
        ramp_mw = net_grid_power_mw - float(prev_grid)
        return ramp_mw * ramp_mw * self.DUCK_CURVE_RAMP_CHARGE_PER_MW2

    def soc_reserve_penalty(
        self, state: dict[str, Any], physics: dict[str, float]
    ) -> float:
        target = self.desired_soc_floor(state)
        shortfall = max(0.0, target - physics["next_soc"])
        return shortfall * shortfall

    def desired_soc_floor(self, state: dict[str, Any]) -> float:
        _ = state
        return 0.0

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))


_DEFAULT_STRATEGY = Strategy()


def controller(state: dict[str, Any]) -> dict[str, float]:
    """Function-style entry point for tools that do not use Strategy classes."""
    return _DEFAULT_STRATEGY.step(state)


if __name__ == "__main__":
    from watt_the_hack.playtest import run_playtest

    result = run_playtest(__file__, "duck_curve", plots=True, open_report=False)
    print(f"\nRaw cost (lower wins): ${result['metrics']['final_score']:,.2f}")
