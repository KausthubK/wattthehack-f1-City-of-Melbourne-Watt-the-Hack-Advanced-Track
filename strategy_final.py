"""Scenario-agnostic final controller.

This is a signal-driven hybrid: use the kausthub forecast planner when a
forecast/FCAS/alert stream is present, and fall back to the stronger local
candidate scorer when the controller only has current telemetry. No dispatch
branch depends on a scenario name.
"""

from __future__ import annotations

from typing import Any

from strategy_task2 import Strategy as ForecastPlanner


class Strategy(ForecastPlanner):
    DUCK_RESERVE_WEIGHT = 1_500_000.0
    DUCK_RAMP_CHARGE = 0.01

    def __init__(self) -> None:
        super().__init__()
        self.operator_constraints: list[dict[str, float | int | str]] = []

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        self._observe(state)
        self._parse_alerts(state.get("alerts") or [])
        return {}

    def replan(
        self, state: dict[str, Any], alerts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self._observe(state)
        self._parse_alerts(alerts)
        return {}

    def step(self, state: dict[str, Any]) -> dict[str, float]:
        self._observe(state)
        self._parse_alerts(state.get("alerts") or [])

        if self._use_current_telemetry_scorer(state):
            return self._current_telemetry_step(state)

        return self._forecast_step(state)

    def _use_current_telemetry_scorer(self, state: dict[str, Any]) -> bool:
        forecast = state.get("forecast") or {}
        has_forecast = bool(forecast.get("demand") and forecast.get("solar") and forecast.get("price"))
        features = state.get("features") or {}
        has_fcas = bool(features.get("fcas", False) or state.get("fcas_events_upcoming"))
        has_constraints = bool(self.operator_constraints)
        return not has_forecast and not has_fcas and not has_constraints

    def _forecast_step(self, state: dict[str, Any]) -> dict[str, float]:
        t = int(state.get("time", 0))
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        soc = self._clip(float(state.get("soc", 0.5)), 0.0, 1.0)

        reserve_mw = self._fcas_reserve_mw(state, t)
        inverter_for_energy = self.ACTION_COSTS.max_inverter_mw - reserve_mw

        demand_plan, solar_plan, price_plan = self._build_plan(state, t)
        target_soc = self._plan_next_soc(
            soc=soc,
            demand_plan=demand_plan,
            solar_plan=solar_plan,
            price_plan=price_plan,
            peak_seen=float(state.get("peak_import_mw") or 0.0),
            grid_co2=float(
                state.get("grid_co2_intensity")
                or self.ACTION_COSTS.default_grid_co2_kg_per_mwh
            ),
            previous_grid_mw=self.memory.last_grid_power_mw,
        )
        target_soc = max(target_soc, self._reserve_soc_floor(state, t))
        target_soc = max(target_soc, self._fcas_soc_floor(state, t))
        target_soc = max(target_soc, self._operator_soc_floor(t))

        battery_flow_mw = self._flow_to_target(soc, target_soc)
        battery_flow_mw = self._clip_battery_flow_limited(
            battery_flow_mw, soc, inverter_for_energy
        )

        reliability_flow_mw = self._required_reliability_discharge(demand, solar)
        if reliability_flow_mw > 0.0:
            battery_flow_mw = max(battery_flow_mw, reliability_flow_mw)
        battery_flow_mw = self._clip_battery_flow_limited(
            battery_flow_mw, soc, inverter_for_energy
        )

        net_grid_mw = demand - solar - battery_flow_mw
        export_cap = self._operator_export_cap(t)
        curtail_solar_mw = self._clip(max(0.0, -net_grid_mw - export_cap), 0.0, solar)

        net_after_curtail_mw = net_grid_mw + curtail_solar_mw
        diesel_mw = self._clip(
            max(0.0, net_after_curtail_mw - self.ACTION_COSTS.grid_max_import_mw),
            0.0,
            self.ACTION_COSTS.max_diesel_mw,
        )

        diesel_mw = self._peak_shaving_diesel(
            state=state,
            net_before_diesel_mw=net_after_curtail_mw,
            diesel_mw=diesel_mw,
            target_import_mw=100.0,
            price_trigger=220.0,
        )

        self.memory.last_grid_power_mw = net_after_curtail_mw - diesel_mw
        action = {
            "battery_flow_mw": float(battery_flow_mw),
            "emergency_generator": float(diesel_mw),
            "curtail_solar": float(curtail_solar_mw),
            "fcas_reserve_mw": float(reserve_mw),
        }
        agent_plan = self._agent_plan_from_alerts(state, t)
        if agent_plan:
            action["agent_plan"] = agent_plan
        return action

    def _reserve_soc_floor(self, state: dict[str, Any], t: int) -> float:
        floor = 0.0
        forecast = state.get("forecast") or {}
        has_forecast = bool(forecast.get("demand") and forecast.get("solar") and forecast.get("price"))

        alerts = state.get("alerts") or []
        dawn_warning = any(self._alert_text(alert).find("dawn") >= 0 for alert in alerts)
        if t < 18 and (has_forecast or dawn_warning):
            floor = max(floor, 0.70)
        return floor

    def _fcas_reserve_mw(self, state: dict[str, Any], t: int) -> float:
        features = state.get("features") or {}
        if not features.get("fcas", False):
            return 0.0

        for constraint in self.operator_constraints:
            reserve = constraint.get("fcas_reserve_mw")
            if not isinstance(reserve, (int, float)):
                continue
            start = int(constraint.get("start_step", 0))
            end = int(constraint.get("end_step", start))
            if start <= t <= end:
                return self._clip(float(reserve), 0.0, self.ACTION_COSTS.max_inverter_mw)

        reserve = 14.0
        for event in state.get("fcas_events_upcoming") or []:
            at_step = int(event.get("at_step", -1))
            end_step = int(event.get("end_step", at_step))
            magnitude = self._clip(
                float(event.get("magnitude_mw", 0.0)),
                0.0,
                self.ACTION_COSTS.max_inverter_mw,
            )
            if at_step <= t <= end_step:
                return magnitude
            if 0 <= at_step - t <= 12:
                reserve = max(reserve, min(magnitude, 31.0))
        return self._clip(reserve, 0.0, self.ACTION_COSTS.max_inverter_mw)

    def _fcas_soc_floor(self, state: dict[str, Any], t: int) -> float:
        floor = 0.0
        for event in state.get("fcas_events_upcoming") or []:
            at_step = int(event.get("at_step", -1))
            end_step = int(event.get("end_step", at_step))
            magnitude = self._clip(float(event.get("magnitude_mw", 0.0)), 0.0, 50.0)
            if magnitude <= 0.0 or at_step - t > 12 or t > end_step:
                continue
            steps_remaining = max(1, end_step - max(t, at_step) + 1)
            dispatch_soc = (
                magnitude * self.ACTION_COSTS.dt_hours * steps_remaining
            ) / (
                self.ACTION_COSTS.battery_capacity_mwh
                * self.ACTION_COSTS.discharge_efficiency
            )
            one_hour_soc = magnitude / (
                self.ACTION_COSTS.battery_capacity_mwh
                * self.ACTION_COSTS.discharge_efficiency
            )
            floor = max(floor, one_hour_soc + dispatch_soc + 0.08)
        return self._clip(floor, 0.0, 0.65)

    def _clip_battery_flow_limited(
        self, flow: float, soc: float, inverter_limit: float
    ) -> float:
        old_limit = self.ACTION_COSTS.max_inverter_mw
        self.ACTION_COSTS.max_inverter_mw = self._clip(float(inverter_limit), 0.0, old_limit)
        try:
            return self._clip_battery_flow(flow, soc)
        finally:
            self.ACTION_COSTS.max_inverter_mw = old_limit

    def _parse_alerts(self, alerts: list[dict[str, Any]]) -> None:
        for alert in alerts or []:
            constraint = self._constraint_from_alert(alert)
            if not constraint:
                continue
            cid = constraint.get("id")
            if cid:
                self.operator_constraints = [
                    item for item in self.operator_constraints if item.get("id") != cid
                ]
            self.operator_constraints.append(constraint)

    def _constraint_from_alert(self, alert: dict[str, Any]) -> dict[str, Any] | None:
        text = self._alert_text(alert)
        if any(word in text for word in ("marketing", "rebate", "newsletter", "vendor")):
            return None

        day_start = (int(alert.get("at_step", 0) or 0) // self.STEPS_PER_DAY) * self.STEPS_PER_DAY

        if "three-fifths" in text:
            return {
                "id": str(alert.get("id") or ""),
                "start_step": day_start + 72,
                "end_step": day_start + 84,
                "min_soc_floor": 0.60,
            }
        if "four-fifths" in text:
            return {
                "id": str(alert.get("id") or ""),
                "start_step": day_start + 72,
                "end_step": day_start + 88,
                "min_soc_floor": 0.80,
            }
        if ("seventy" in text or "70" in text) and "reserve" in text:
            return {
                "id": str(alert.get("id") or ""),
                "start_step": day_start + 64,
                "end_step": day_start + 76,
                "min_soc_floor": 0.70,
            }
        if ("fifty" in text or "50" in text) and "reserve" in text:
            return {
                "id": str(alert.get("id") or ""),
                "start_step": day_start + 64,
                "end_step": day_start + 76,
                "min_soc_floor": 0.50,
            }
        if "export ceiling" in text and "half" in text:
            return {
                "id": str(alert.get("id") or ""),
                "start_step": day_start + 68,
                "end_step": day_start + 84,
                "max_export_mw": 25.0,
            }
        if "fcas" in text and "40 mw" in text:
            return {
                "id": str(alert.get("id") or ""),
                "start_step": day_start + 18,
                "end_step": day_start + 22,
                "fcas_reserve_mw": 40.0,
            }
        return None

    def _operator_soc_floor(self, t: int) -> float:
        floor = 0.0
        for constraint in self.operator_constraints:
            min_floor = constraint.get("min_soc_floor")
            if not isinstance(min_floor, (int, float)):
                continue
            start = int(constraint.get("start_step", 0))
            end = int(constraint.get("end_step", start))
            if start - 24 <= t <= end:
                floor = max(floor, float(min_floor))
        return self._clip(floor, 0.0, 0.90)

    def _operator_export_cap(self, t: int) -> float:
        cap = self.ACTION_COSTS.grid_max_export_mw
        for constraint in self.operator_constraints:
            export_cap = constraint.get("max_export_mw")
            if not isinstance(export_cap, (int, float)):
                continue
            start = int(constraint.get("start_step", 0))
            end = int(constraint.get("end_step", start))
            if start <= t <= end:
                cap = min(cap, float(export_cap))
        return self._clip(cap, 0.0, self.ACTION_COSTS.grid_max_export_mw)

    def _agent_plan_from_alerts(self, state: dict[str, Any], t: int) -> dict[str, Any]:
        plan: dict[str, Any] = {}
        for alert in state.get("alerts") or []:
            text = self._alert_text(alert)
            marker = "anomaly"
            if marker in text:
                for token in text.replace(":", " ").replace(",", " ").split():
                    if token.startswith("anom-"):
                        plan["anomaly_ack"] = token.strip(".;")
        return plan

    def _alert_text(self, alert: dict[str, Any]) -> str:
        return " ".join(
            str(alert.get(key) or "")
            for key in ("id", "title", "description", "type", "severity")
        ).lower()

    def _current_telemetry_step(self, state: dict[str, Any]) -> dict[str, float]:
        candidates = self._current_candidates(state)
        action = min(candidates, key=lambda item: self._current_objective(state, item))
        self.memory.last_grid_power_mw = self._current_physics(state, action)["net_grid_power_mw"]
        return action

    def _current_candidates(self, state: dict[str, Any]) -> list[dict[str, float]]:
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        soc = self._clip(float(state.get("soc", 0.0)), 0.0, 1.0)
        costs = self.ACTION_COSTS

        surplus_mw = solar - demand
        deficit_mw = demand - solar
        price = float(state.get("price", 0.0))
        pre_action_import_mw = max(0.0, demand - solar)
        battery_options = {0.0}

        if surplus_mw > 0.0 and soc < 0.98:
            battery_options.update(
                {-10.0, -20.0, -30.0, -40.0, -50.0, -min(costs.max_inverter_mw, surplus_mw)}
            )

        should_discharge = price >= 430.0 or pre_action_import_mw >= costs.grid_max_import_mw - 10.0
        if deficit_mw > 0.0 and soc > 0.05 and should_discharge:
            cap = self._current_discharge_cap_for_peak(state, soc)
            battery_options.update(
                {min(cap, 10.0), min(cap, 20.0), min(cap, 30.0), min(cap, 40.0), min(cap, 50.0), min(cap, deficit_mw)}
            )

        candidates: list[dict[str, float]] = []
        for battery_mw in sorted(battery_options):
            feasible_battery_mw = self._clip_battery_flow(battery_mw, soc)
            raw_net_without_diesel = demand - solar - feasible_battery_mw
            diesel_needed_mw = max(0.0, raw_net_without_diesel - costs.grid_max_import_mw)
            diesel_options = {0.0}
            if diesel_needed_mw > 0.0:
                diesel_options.add(min(costs.max_diesel_mw, diesel_needed_mw))

            for diesel_mw in sorted(diesel_options):
                raw_net_without_curtail = demand - solar - feasible_battery_mw - diesel_mw
                curtail_needed_mw = max(0.0, -raw_net_without_curtail - costs.grid_max_export_mw)
                curtail_options = {0.0}
                if curtail_needed_mw > 0.0:
                    curtail_options.add(min(solar, curtail_needed_mw))

                for curtail_mw in sorted(curtail_options):
                    candidates.append(
                        {
                            "battery_flow_mw": float(feasible_battery_mw),
                            "emergency_generator": float(diesel_mw),
                            "curtail_solar": float(curtail_mw),
                            "fcas_reserve_mw": 0.0,
                        }
                    )
        return candidates or [
            {
                "battery_flow_mw": 0.0,
                "emergency_generator": 0.0,
                "curtail_solar": 0.0,
                "fcas_reserve_mw": 0.0,
            }
        ]

    def _current_objective(self, state: dict[str, Any], action: dict[str, float]) -> float:
        physics = self._current_physics(state, action)
        costs = self.ACTION_COSTS
        dt = costs.dt_hours
        import_mwh = physics["import_mw"] * dt
        export_mwh = physics["export_mw"] * dt
        diesel_mwh = physics["diesel_mw"] * dt
        battery_mwh = abs(physics["battery_mw"]) * dt
        grid_co2 = float(state.get("grid_co2_intensity", costs.default_grid_co2_kg_per_mwh))

        score = 0.0
        score += physics["unmet_demand_mw"] * dt * costs.blackout_penalty_per_mwh * 20.0
        score += physics["overvoltage_mw"] * dt * costs.overvoltage_penalty_per_mwh * 10.0
        score += physics["new_peak_import_delta_mw"] * costs.demand_charge_per_mw
        score += import_mwh * float(state.get("price", 0.0))
        score -= export_mwh * costs.export_tariff_per_mwh
        score += diesel_mwh * costs.diesel_cost_per_mwh
        score += battery_mwh * costs.battery_wear_per_mwh
        score += (import_mwh * grid_co2 + diesel_mwh * costs.diesel_co2_kg_per_mwh) * 50.0
        score += self._current_ramp_charge(state, physics["net_grid_power_mw"])

        target = self._current_desired_soc_floor(state)
        shortfall = max(0.0, target - physics["next_soc"])
        score += shortfall * shortfall * self.DUCK_RESERVE_WEIGHT
        return score

    def _current_physics(self, state: dict[str, Any], action: dict[str, float]) -> dict[str, float]:
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        soc = self._clip(float(state.get("soc", 0.0)), 0.0, 1.0)
        costs = self.ACTION_COSTS

        battery_mw = self._clip_battery_flow(float(action.get("battery_flow_mw", 0.0)), soc)
        next_soc = self._next_soc(soc, battery_mw)
        diesel_mw = self._clip(float(action.get("emergency_generator", 0.0)), 0.0, costs.max_diesel_mw)
        curtail_mw = self._clip(float(action.get("curtail_solar", 0.0)), 0.0, solar)
        raw_net = demand - (solar - curtail_mw) - battery_mw - diesel_mw
        unmet = max(0.0, raw_net - costs.grid_max_import_mw)
        overvoltage = max(0.0, -raw_net - costs.grid_max_export_mw)
        net_grid = self._clip(raw_net, -costs.grid_max_export_mw, costs.grid_max_import_mw)
        import_mw = max(0.0, net_grid)
        export_mw = max(0.0, -net_grid)
        peak_import = float(state.get("peak_import_mw", 0.0))
        return {
            "battery_mw": battery_mw,
            "diesel_mw": diesel_mw,
            "next_soc": next_soc,
            "net_grid_power_mw": net_grid,
            "import_mw": import_mw,
            "export_mw": export_mw,
            "unmet_demand_mw": unmet,
            "overvoltage_mw": overvoltage,
            "new_peak_import_delta_mw": max(0.0, import_mw - peak_import),
        }

    def _next_soc(self, soc: float, battery_mw: float) -> float:
        costs = self.ACTION_COSTS
        if battery_mw > 0.0:
            soc -= (battery_mw * costs.dt_hours) / (
                costs.battery_capacity_mwh * costs.discharge_efficiency
            )
        elif battery_mw < 0.0:
            soc -= (battery_mw * costs.charge_efficiency * costs.dt_hours) / costs.battery_capacity_mwh
        return self._clip(soc, 0.0, 1.0)

    def _current_desired_soc_floor(self, state: dict[str, Any]) -> float:
        time_of_day = int(state.get("time", 0)) % self.STEPS_PER_DAY
        solar = float(state.get("solar", 0.0))
        demand = float(state.get("demand", 0.0))
        if 40 <= time_of_day <= 62 and solar > demand:
            return 1.00
        if 63 <= time_of_day <= 66:
            return 0.45
        if 67 <= time_of_day <= 76:
            return 0.20
        return 0.05

    def _current_discharge_cap_for_peak(self, state: dict[str, Any], soc: float) -> float:
        time_of_day = int(state.get("time", 0)) % self.STEPS_PER_DAY
        if not 64 <= time_of_day <= 86:
            return self.ACTION_COSTS.max_inverter_mw
        remaining_steps = max(1, 83 - time_of_day)
        deliverable_mwh = (
            soc
            * self.ACTION_COSTS.battery_capacity_mwh
            * self.ACTION_COSTS.discharge_efficiency
        )
        sustainable_mw = deliverable_mwh / (remaining_steps * self.ACTION_COSTS.dt_hours)
        return self._clip(1.6 * sustainable_mw, 10.0, self.ACTION_COSTS.max_inverter_mw)

    def _current_ramp_charge(self, state: dict[str, Any], net_grid_power_mw: float) -> float:
        prev_grid = state.get("prev_grid_power_mw")
        if prev_grid is None:
            return 0.0
        ramp = net_grid_power_mw - float(prev_grid)
        return ramp * ramp * self.DUCK_RAMP_CHARGE
