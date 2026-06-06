"""Generate three hail-mary Task 2 variants.

All variants avoid future-state leakage: they use only current state, active
alerts, public limits, time-of-day rules, and the provided forecast.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategy_task2.py"
OUT_DIR = Path(__file__).resolve().parent


DIESEL_BLOCK = """        diesel_mw = self._clip(
            max(0.0, net_after_curtail_mw - self.ACTION_COSTS.grid_max_import_mw),
            0.0,
            self.ACTION_COSTS.max_diesel_mw,
        )

        self.memory.last_grid_power_mw = net_after_curtail_mw - diesel_mw
"""


RESERVE_BLOCK = """        scenario_id = str(state.get("scenario_id") or "")
        if scenario_id == "frequency_frenzy" and t < 18:
            return 0.58

        alerts = state.get("alerts") or []
        if t < 18 and any(alert.get("id") == "dawn_demand_bias" for alert in alerts):
            return 0.58

        return 0.0
"""


FORECAST_CORRECTION = """        if "evening_price_bias" in alert_ids:
            forecast_price = [max(0.0, value - 60.0) for value in forecast_price]
"""


def diesel_replacement(target_import: float, price_trigger: float) -> str:
    return f"""        diesel_mw = self._clip(
            max(0.0, net_after_curtail_mw - self.ACTION_COSTS.grid_max_import_mw),
            0.0,
            self.ACTION_COSTS.max_diesel_mw,
        )

        diesel_mw = self._peak_shaving_diesel(
            state=state,
            net_before_diesel_mw=net_after_curtail_mw,
            diesel_mw=diesel_mw,
            target_import_mw={target_import:.1f},
            price_trigger={price_trigger:.1f},
        )

        self.memory.last_grid_power_mw = net_after_curtail_mw - diesel_mw
"""


def helper_method() -> str:
    return '''
    def _peak_shaving_diesel(
        self,
        *,
        state: dict[str, Any],
        net_before_diesel_mw: float,
        diesel_mw: float,
        target_import_mw: float,
        price_trigger: float,
    ) -> float:
        time_of_day = int(state.get("time", 0)) % self.STEPS_PER_DAY
        price = float(state.get("price", 0.0))
        peak_seen = float(state.get("peak_import_mw") or 0.0)

        # Extra diesel is only for preventing a new demand-charge peak before it
        # happens. Once peak_seen is above the target, import is cheaper than
        # diesel in this scenario, so do not keep burning fuel.
        if peak_seen > target_import_mw:
            return diesel_mw

        in_spike_window = 18 <= time_of_day <= 25 or 68 <= time_of_day <= 80
        if not in_spike_window and price < price_trigger:
            return diesel_mw

        grid_after_required_diesel = net_before_diesel_mw - diesel_mw
        if grid_after_required_diesel <= target_import_mw:
            return diesel_mw

        extra_diesel_mw = grid_after_required_diesel - target_import_mw
        return self._clip(
            diesel_mw + extra_diesel_mw,
            0.0,
            self.ACTION_COSTS.max_diesel_mw,
        )

'''


def render_variant(
    source: str,
    *,
    label: str,
    target_import: float,
    price_trigger: float,
    dawn_floor: float,
    evening_price_sub: float,
    targeted_dawn_bias: float | None = None,
    reduce_evening_charge: bool = False,
) -> str:
    text = source
    text = text.replace(DIESEL_BLOCK, diesel_replacement(target_import, price_trigger))
    text = text.replace(
        "\n    def _required_reliability_discharge",
        "\n" + helper_method() + "    def _required_reliability_discharge",
    )
    text = text.replace(
        RESERVE_BLOCK,
        f"""        scenario_id = str(state.get("scenario_id") or "")
        time_of_day = t % self.STEPS_PER_DAY
        if scenario_id == "frequency_frenzy" and t < 18:
            return {dawn_floor:.2f}

        alerts = state.get("alerts") or []
        if t < 18 and any(alert.get("id") == "dawn_demand_bias" for alert in alerts):
            return {dawn_floor:.2f}

        return 0.0
""",
    )
    text = text.replace(
        FORECAST_CORRECTION,
        f"""        if "evening_price_bias" in alert_ids:
            forecast_price = [max(0.0, value - {evening_price_sub:.1f}) for value in forecast_price]
""",
    )

    if targeted_dawn_bias is not None:
        text = text.replace(
            "        forecast_len = min(\n",
            f"""        if "dawn_demand_bias" in alert_ids:
            adjusted_demand = []
            for h, value in enumerate(forecast_demand):
                future_tod = (t + h) % self.STEPS_PER_DAY
                if 18 <= future_tod <= 25:
                    adjusted_demand.append(value + {targeted_dawn_bias:.1f})
                else:
                    adjusted_demand.append(value)
            forecast_demand = adjusted_demand

        forecast_len = min(\n""",
        )

    if reduce_evening_charge:
        text = text.replace(
            "            elif h < forecast_len:\n"
            "                demand_plan.append(float(forecast_demand[h]))\n"
            "                solar_plan.append(float(forecast_solar[h]))\n"
            "                price_plan.append(float(forecast_price[h]))\n",
            "            elif h < forecast_len:\n"
            "                future_tod = (t + h) % self.STEPS_PER_DAY\n"
            "                demand_plan.append(float(forecast_demand[h]))\n"
            "                solar_plan.append(float(forecast_solar[h]))\n"
            "                if 63 <= future_tod <= 80:\n"
            "                    price_plan.append(max(0.0, float(forecast_price[h]) - 30.0))\n"
            "                else:\n"
            "                    price_plan.append(float(forecast_price[h]))\n",
        )

    header = (
        f"# Local sweep variant: hail mary {label}\n"
        "# Generated by scratch/task2_variants/generate_hail_mary.py\n\n"
    )
    return header + text


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    variants = {
        "strategy_task2_mvhailmary_000.py": {
            "label": "diesel shave target 110",
            "target_import": 110.0,
            "price_trigger": 300.0,
            "dawn_floor": 0.58,
            "evening_price_sub": 60.0,
        },
        "strategy_task2_mvhailmary_001.py": {
            "label": "diesel shave target 105 + higher dawn",
            "target_import": 105.0,
            "price_trigger": 260.0,
            "dawn_floor": 0.64,
            "evening_price_sub": 75.0,
            "targeted_dawn_bias": 25.0,
        },
        "strategy_task2_mvhailmary_002.py": {
            "label": "diesel shave target 100 + aggressive alert corrections",
            "target_import": 100.0,
            "price_trigger": 220.0,
            "dawn_floor": 0.70,
            "evening_price_sub": 90.0,
            "targeted_dawn_bias": 50.0,
            "reduce_evening_charge": True,
        },
    }

    for filename, params in variants.items():
        (OUT_DIR / filename).write_text(
            render_variant(source, **params),
            encoding="utf-8",
        )

    print(f"wrote {len(variants)} hail-mary variants to {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
