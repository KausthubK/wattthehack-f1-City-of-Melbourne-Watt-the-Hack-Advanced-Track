"""Final hybrid strategy for the Watt-The-Hack scenarios.

The base class is the current submission-5 controller, which already handles
FCAS, operator directives, and cyber/anomaly bookkeeping. This wrapper only
changes the low-mechanic dispatch where the kausthub-v1 task strategies were
stronger: duck-style scenarios use a conservative candidate scorer, and
Frequency Frenzy keeps a larger dawn reserve plus modest peak-shaving diesel.
"""

from __future__ import annotations

from typing import Any

from watt_the_hack.strategy import Strategy as SubmissionFiveStrategy
from watt_the_hack.strategy import clamp


class Strategy(SubmissionFiveStrategy):
    DUCK_RESERVE_WEIGHT = 1_500_000.0
    DUCK_RAMP_CHARGE = 0.01
    MAX_DIESEL_MW = 50.0

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        return self.replan(state, state.get("alerts") or [])

    def replan(
        self, state: dict[str, Any], alerts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        scenario_id = str(state.get("scenario_id") or "")
        if scenario_id == "agentic_demo":
            for alert in alerts or []:
                constraint = self._agentic_demo_constraint(alert)
                if constraint:
                    self._remember_operator_constraint(constraint)
            return {}
        return super().replan(state, alerts)

    def step(self, state: dict[str, Any]) -> dict[str, float]:
        scenario_id = str(state.get("scenario_id") or "")
        if scenario_id in {"duck_curve", "agentic_demo"}:
            return self._duck_style_step(state)

        action = super().step(state)
        if scenario_id == "frequency_frenzy":
            action = self._frequency_peak_shave(state, action)
        return action

    def _build_plan(self, state, t):
        if str(state.get("scenario_id") or "") != "frequency_frenzy":
            return super()._build_plan(state, t)

        forecast = state.get("forecast") or {}
        forecast_demand = list(forecast.get("demand") or [])
        forecast_solar = list(forecast.get("solar") or [])
        forecast_price = list(forecast.get("price") or [])
        alert_ids = {alert.get("id") for alert in state.get("alerts", [])}

        if "evening_price_bias" in alert_ids:
            forecast_price = [max(0.0, value - 90.0) for value in forecast_price]
        if "dawn_demand_bias" in alert_ids:
            adjusted = []
            for h, value in enumerate(forecast_demand):
                future_tod = (t + h) % self.STEPS_PER_DAY
                adjusted.append(value + 50.0 if 18 <= future_tod <= 25 else value)
            forecast_demand = adjusted

        forecast_len = min(len(forecast_demand), len(forecast_solar), len(forecast_price))
        demand_plan, solar_plan, price_plan = [], [], []
        for h in range(self.PLAN_HORIZON):
            if h == 0:
                demand_plan.append(float(state.get("demand", 0.0)))
                solar_plan.append(float(state.get("solar", 0.0)))
                price_plan.append(float(state.get("price", 0.0)))
            elif h < forecast_len:
                future_tod = (t + h) % self.STEPS_PER_DAY
                demand_plan.append(float(forecast_demand[h]))
                solar_plan.append(float(forecast_solar[h]))
                price = float(forecast_price[h])
                price_plan.append(max(0.0, price - 30.0) if 63 <= future_tod <= 80 else price)
            else:
                slot = (t + h) % self.STEPS_PER_DAY
                demand_plan.append(self.demand_profile[slot])
                solar_plan.append(self.solar_profile[slot])
                price_plan.append(self.price_profile[slot])
        return demand_plan, solar_plan, price_plan

    def _reserve_soc_floor(self, state, t):
        floor = super()._reserve_soc_floor(state, t)
        scenario_id = str(state.get("scenario_id") or "")

        if scenario_id == "frequency_frenzy" and t < 18:
            floor = max(floor, 0.70)
        alerts = state.get("alerts") or []
        if t < 18 and any(alert.get("id") == "dawn_demand_bias" for alert in alerts):
            floor = max(floor, 0.70)
        if scenario_id == "agentic_demo":
            floor = max(floor, self._operator_soc_floor(t))
        return clamp(floor, 0.0, 0.90)

    def _frequency_peak_shave(self, state, action):
        t = int(state.get("time", 0))
        time_of_day = t % self.STEPS_PER_DAY
        price = float(state.get("price", 0.0))
        peak_seen = float(state.get("peak_import_mw") or 0.0)
        if peak_seen > 100.0:
            return action
        if not (18 <= time_of_day <= 25 or 68 <= time_of_day <= 80) and price < 220.0:
            return action

        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        flow = float(action.get("battery_flow_mw", 0.0))
        curtail = float(action.get("curtail_solar", 0.0))
        diesel = float(action.get("emergency_generator", 0.0))
        net_after_curtail = demand - solar - flow + curtail
        grid_after_diesel = net_after_curtail - diesel
        if grid_after_diesel <= 100.0:
            return action

        action = dict(action)
        action["emergency_generator"] = clamp(
            diesel + grid_after_diesel - 100.0,
            0.0,
            self.MAX_DIESEL_MW,
        )
        self.last_grid_mw = net_after_curtail - action["emergency_generator"]
        return action

    def _duck_style_step(self, state):
        candidates = self._duck_candidate_actions(state)
        action = min(candidates, key=lambda item: self._duck_objective_score(state, item))
        self.last_grid_mw = self._duck_physics(state, action)["net_grid_power_mw"]
        return action

    def _duck_candidate_actions(self, state):
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        soc = clamp(float(state.get("soc", 0.5)), 0.0, 1.0)
        price = float(state.get("price", 0.0))
        surplus_mw = solar - demand
        deficit_mw = demand - solar
        pre_action_import_mw = max(0.0, deficit_mw)

        battery_options = {0.0}
        if surplus_mw > 0.0 and soc < 0.98:
            battery_options.update(
                {-10.0, -20.0, -30.0, -40.0, -50.0, -min(self.INVERTER_MW, surplus_mw)}
            )

        should_discharge = price >= 430.0 or pre_action_import_mw >= self.GRID_IMPORT_CAP_MW - 10.0
        if deficit_mw > 0.0 and soc > 0.05 and should_discharge:
            cap = self._duck_discharge_cap_for_peak(state, soc)
            battery_options.update(
                {min(cap, 10.0), min(cap, 20.0), min(cap, 30.0), min(cap, 40.0), min(cap, 50.0), min(cap, deficit_mw)}
            )

        candidates = []
        for battery_mw in sorted(battery_options):
            battery_mw = self._clip_battery_flow(battery_mw, soc)
            raw_net = demand - solar - battery_mw
            diesel_needed = max(0.0, raw_net - self.GRID_IMPORT_CAP_MW)
            diesel_options = {0.0}
            if diesel_needed > 0.0:
                diesel_options.add(min(self.MAX_DIESEL_MW, diesel_needed))

            for diesel_mw in sorted(diesel_options):
                raw_net_without_curtail = demand - solar - battery_mw - diesel_mw
                curtail_needed = max(0.0, -raw_net_without_curtail - self.GRID_EXPORT_CAP_MW)
                curtail_options = {0.0}
                if curtail_needed > 0.0:
                    curtail_options.add(min(solar, curtail_needed))
                for curtail_mw in sorted(curtail_options):
                    candidates.append(
                        {
                            "battery_flow_mw": float(battery_mw),
                            "emergency_generator": float(diesel_mw),
                            "curtail_solar": float(curtail_mw),
                            "fcas_reserve_mw": 0.0,
                        }
                    )
        return candidates or [{"battery_flow_mw": 0.0, "emergency_generator": 0.0, "curtail_solar": 0.0, "fcas_reserve_mw": 0.0}]

    def _duck_objective_score(self, state, action):
        physics = self._duck_physics(state, action)
        dt = self.DT_HOURS
        import_mwh = physics["import_mw"] * dt
        export_mwh = physics["export_mw"] * dt
        diesel_mwh = physics["diesel_mw"] * dt
        battery_mwh = abs(physics["battery_mw"]) * dt
        grid_co2 = float(state.get("grid_co2_intensity", self.GRID_CO2_KG_PER_MWH))

        score = 0.0
        score += physics["unmet_demand_mw"] * dt * 100_000.0 * 20.0
        score += physics["overvoltage_mw"] * dt * 5_000.0 * 10.0
        score += physics["new_peak_import_delta_mw"] * self.DEMAND_CHARGE_PER_MW
        score += import_mwh * float(state.get("price", 0.0))
        score -= export_mwh * self.EXPORT_TARIFF
        score += diesel_mwh * self.DIESEL_PER_MWH
        score += battery_mwh * self.BATTERY_WEAR_PER_MWH
        score += (import_mwh * grid_co2 + diesel_mwh * self.DIESEL_CO2_KG_PER_MWH) * 50.0
        score += self._duck_ramp_charge(state, physics["net_grid_power_mw"])

        target = self._duck_desired_soc_floor(state)
        shortfall = max(0.0, target - physics["next_soc"])
        score += shortfall * shortfall * self.DUCK_RESERVE_WEIGHT
        return score

    def _duck_physics(self, state, action):
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        soc = clamp(float(state.get("soc", 0.5)), 0.0, 1.0)
        battery_mw = self._clip_battery_flow(float(action.get("battery_flow_mw", 0.0)), soc)
        next_soc = self._estimate_next_soc(soc, battery_mw, state, int(state.get("time", 0)), 0.0)
        diesel_mw = clamp(
            float(action.get("emergency_generator", 0.0)),
            0.0,
            self.MAX_DIESEL_MW,
        )
        curtail_mw = clamp(float(action.get("curtail_solar", 0.0)), 0.0, solar)

        raw_net = demand - (solar - curtail_mw) - battery_mw - diesel_mw
        unmet = max(0.0, raw_net - self.GRID_IMPORT_CAP_MW)
        overvoltage = max(0.0, -raw_net - self.GRID_EXPORT_CAP_MW)
        net_grid = clamp(raw_net, -self.GRID_EXPORT_CAP_MW, self.GRID_IMPORT_CAP_MW)
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

    def _duck_desired_soc_floor(self, state):
        t = int(state.get("time", 0))
        time_of_day = t % self.STEPS_PER_DAY
        solar = float(state.get("solar", 0.0))
        demand = float(state.get("demand", 0.0))
        floor = 0.05

        if 40 <= time_of_day <= 62 and solar > demand:
            floor = 1.00
        elif 63 <= time_of_day <= 66:
            floor = 0.45
        elif 67 <= time_of_day <= 76:
            floor = 0.20
        elif 77 <= time_of_day <= 86:
            floor = 0.05

        scenario_id = str(state.get("scenario_id") or "")
        if scenario_id == "agentic_demo":
            floor = max(floor, self._operator_soc_floor(t))
        return floor

    def _duck_discharge_cap_for_peak(self, state, soc):
        time_of_day = int(state.get("time", 0)) % self.STEPS_PER_DAY
        if not 64 <= time_of_day <= 86:
            return self.INVERTER_MW
        remaining_steps = max(1, 83 - time_of_day)
        deliverable_mwh = soc * self.BATTERY_MWH * self.DISCHARGE_EFF
        sustainable_mw = deliverable_mwh / (remaining_steps * self.DT_HOURS)
        return clamp(1.6 * sustainable_mw, 10.0, self.INVERTER_MW)

    def _duck_ramp_charge(self, state, net_grid_power_mw):
        prev_grid = state.get("prev_grid_power_mw")
        if prev_grid is None:
            return 0.0
        ramp = net_grid_power_mw - float(prev_grid)
        return ramp * ramp * self.DUCK_RAMP_CHARGE

    def _agentic_demo_constraint(self, alert):
        text = " ".join(
            str(alert.get(key) or "")
            for key in ("id", "title", "description", "type", "severity")
        ).lower()
        if "vendor" in text or "newsletter" in text or "no action required" in text:
            return None
        if "reserve" not in text and "state-of-charge" not in text:
            return None

        floor = None
        if "seventy" in text or "70" in text:
            floor = 0.70
        elif "fifty" in text or "50" in text:
            floor = 0.50
        if floor is None:
            return None

        day_start = (int(alert.get("at_step", 0) or 0) // self.STEPS_PER_DAY) * self.STEPS_PER_DAY
        return {
            "id": str(alert.get("id") or ""),
            "start_step": day_start + 64,
            "end_step": day_start + 76,
            "min_soc_floor": floor,
        }


if __name__ == "__main__":
    from watt_the_hack.playtest import run_playtest

    result = run_playtest(__file__, "duck_curve", plots=True, open_report=False)
    print(f"\nRaw cost (lower wins): ${result['metrics']['final_score']:,.2f}")
