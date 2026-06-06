"""Generate local-only variants for Task 2 / frequency_frenzy sweeps."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategy_task2.py"
OUT_DIR = Path(__file__).resolve().parent


FORECAST_LEN_BLOCK = """        forecast_len = min(
            len(forecast_demand),
            len(forecast_solar),
            len(forecast_price),
        )
"""


FORECAST_PRICE_LINE = """        forecast_price = list(forecast.get("price") or [])
"""


RESERVE_BLOCK = """        scenario_id = str(state.get("scenario_id") or "")
        if scenario_id == "frequency_frenzy" and t < 18:
            return 0.50

        alerts = state.get("alerts") or []
        if t < 18 and any(alert.get("id") == "dawn_demand_bias" for alert in alerts):
            return 0.50

        return 0.0
"""


def render_variant(
    source: str,
    *,
    label: str,
    carbon_price: float | None = None,
    ramp_charge: float | None = None,
    battery_wear: float | None = None,
    future_demand_factor: float | None = None,
    soc_grid_points: int | None = None,
    plan_horizon: int | None = None,
    forecast_trust_steps: int | None = None,
    dawn_bias_add_mw: float | None = None,
    evening_price_sub: float | None = None,
    dawn_floor: float | None = None,
    post_dawn_floor: float | None = None,
    midday_floor: float | None = None,
    future_demand_peak_mw: float | None = None,
) -> str:
    text = source

    if carbon_price is not None:
        text = text.replace(
            "self.carbon_price_per_kg = 125.0",
            f"self.carbon_price_per_kg = {carbon_price:.1f}",
        )
    if ramp_charge is not None:
        text = text.replace(
            "self.ramp_charge_per_mw2 = 0.75",
            f"self.ramp_charge_per_mw2 = {ramp_charge:.2f}",
        )
    if battery_wear is not None:
        text = text.replace(
            "self.battery_wear_per_mwh = 50.0",
            f"self.battery_wear_per_mwh = {battery_wear:.1f}",
        )
    if future_demand_factor is not None:
        text = text.replace(
            "demand_charge_rate *= 0.25",
            f"demand_charge_rate *= {future_demand_factor:.2f}",
        )
    if soc_grid_points is not None:
        text = text.replace(
            "SOC_GRID_POINTS = 31",
            f"SOC_GRID_POINTS = {soc_grid_points}",
        )
    if plan_horizon is not None:
        text = text.replace("PLAN_HORIZON = 96", f"PLAN_HORIZON = {plan_horizon}")

    if forecast_trust_steps is not None:
        text = text.replace(
            FORECAST_LEN_BLOCK,
            FORECAST_LEN_BLOCK
            + f"        forecast_len = min(forecast_len, {forecast_trust_steps})\n",
        )

    if dawn_bias_add_mw is not None or evening_price_sub is not None:
        correction = "        alert_ids = {alert.get(\"id\") for alert in state.get(\"alerts\", [])}\n"
        if dawn_bias_add_mw is not None:
            correction += (
                "        if \"dawn_demand_bias\" in alert_ids:\n"
                f"            forecast_demand = [value + {dawn_bias_add_mw:.1f} for value in forecast_demand]\n"
            )
        if evening_price_sub is not None:
            correction += (
                "        if \"evening_price_bias\" in alert_ids:\n"
                f"            forecast_price = [max(0.0, value - {evening_price_sub:.1f}) for value in forecast_price]\n"
            )
        text = text.replace(FORECAST_PRICE_LINE, FORECAST_PRICE_LINE + correction)

    if (
        dawn_floor is not None
        or post_dawn_floor is not None
        or midday_floor is not None
        or future_demand_peak_mw is not None
    ):
        dawn = 0.50 if dawn_floor is None else dawn_floor
        extra = ""
        if post_dawn_floor is not None:
            extra += (
                f"        if scenario_id == \"frequency_frenzy\" and 26 <= time_of_day <= 35:\n"
                f"            return {post_dawn_floor:.2f}\n\n"
            )
        if midday_floor is not None:
            extra += (
                f"        if scenario_id == \"frequency_frenzy\" and 36 <= time_of_day <= 62 and float(state.get(\"solar\", 0.0)) > float(state.get(\"demand\", 0.0)):\n"
                f"            return {midday_floor:.2f}\n\n"
            )
        if future_demand_peak_mw is not None:
            extra += (
                f"        if scenario_id == \"frequency_frenzy\" and 10 <= time_of_day < 18:\n"
                f"            return max({dawn:.2f}, float(state.get(\"soc\", 0.0)))\n\n"
            )
        text = text.replace(
            RESERVE_BLOCK,
            f"""        scenario_id = str(state.get("scenario_id") or "")
        time_of_day = t % self.STEPS_PER_DAY
        if scenario_id == "frequency_frenzy" and t < 18:
            return {dawn:.2f}

        alerts = state.get("alerts") or []
        if t < 18 and any(alert.get("id") == "dawn_demand_bias" for alert in alerts):
            return {dawn:.2f}

{extra}        return 0.0
""",
        )

    if future_demand_peak_mw is not None:
        text = text.replace(
            "if high_net_soon > max(peak_seen, 80.0):",
            f"if high_net_soon > max(peak_seen, {future_demand_peak_mw:.1f}):",
        )

    header = (
        f"# Local sweep variant: {label}\n"
        "# Generated by scratch/task2_variants/generate_variants.py\n\n"
    )
    return header + text


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    variants: dict[str, dict] = {
        "strategy_task2_mvbaseline.py": {"label": "current strategy_task2.py"},
        "strategy_task2_mvbatchA_000.py": {
            "label": "carbon=50",
            "carbon_price": 50.0,
        },
        "strategy_task2_mvbatchA_001.py": {
            "label": "carbon=75",
            "carbon_price": 75.0,
        },
        "strategy_task2_mvbatchA_002.py": {
            "label": "carbon=100",
            "carbon_price": 100.0,
        },
        "strategy_task2_mvbatchA_003.py": {
            "label": "carbon=150",
            "carbon_price": 150.0,
        },
        "strategy_task2_mvbatchA_004.py": {
            "label": "ramp=0.25",
            "ramp_charge": 0.25,
        },
        "strategy_task2_mvbatchA_005.py": {
            "label": "ramp=1.50",
            "ramp_charge": 1.50,
        },
        "strategy_task2_mvbatchA_006.py": {
            "label": "wear=25",
            "battery_wear": 25.0,
        },
        "strategy_task2_mvbatchA_007.py": {
            "label": "wear=100",
            "battery_wear": 100.0,
        },
        "strategy_task2_mvbatchA_008.py": {
            "label": "future demand factor=0.10",
            "future_demand_factor": 0.10,
        },
        "strategy_task2_mvbatchA_009.py": {
            "label": "future demand factor=0.50",
            "future_demand_factor": 0.50,
        },
        "strategy_task2_mvbatchB_000.py": {
            "label": "forecast trust 6",
            "forecast_trust_steps": 6,
        },
        "strategy_task2_mvbatchB_001.py": {
            "label": "forecast trust 18",
            "forecast_trust_steps": 18,
        },
        "strategy_task2_mvbatchB_002.py": {
            "label": "dawn demand bias +30",
            "dawn_bias_add_mw": 30.0,
        },
        "strategy_task2_mvbatchB_003.py": {
            "label": "dawn demand bias +60",
            "dawn_bias_add_mw": 60.0,
        },
        "strategy_task2_mvbatchB_004.py": {
            "label": "evening price bias -30",
            "evening_price_sub": 30.0,
        },
        "strategy_task2_mvbatchB_005.py": {
            "label": "evening price bias -60",
            "evening_price_sub": 60.0,
        },
        "strategy_task2_mvbatchB_006.py": {
            "label": "forecast trust 6 + dawn bias +60",
            "forecast_trust_steps": 6,
            "dawn_bias_add_mw": 60.0,
        },
        "strategy_task2_mvbatchB_007.py": {
            "label": "forecast trust 18 + evening price -60",
            "forecast_trust_steps": 18,
            "evening_price_sub": 60.0,
        },
        "strategy_task2_mvbatchC_000.py": {
            "label": "dawn floor 0.45",
            "dawn_floor": 0.45,
        },
        "strategy_task2_mvbatchC_001.py": {
            "label": "dawn floor 0.55",
            "dawn_floor": 0.55,
        },
        "strategy_task2_mvbatchC_002.py": {
            "label": "dawn floor 0.60",
            "dawn_floor": 0.60,
        },
        "strategy_task2_mvbatchC_003.py": {
            "label": "post dawn floor 0.25",
            "post_dawn_floor": 0.25,
        },
        "strategy_task2_mvbatchC_004.py": {
            "label": "post dawn floor 0.35",
            "post_dawn_floor": 0.35,
        },
        "strategy_task2_mvbatchC_005.py": {
            "label": "midday surplus floor 0.85",
            "midday_floor": 0.85,
        },
        "strategy_task2_mvbatchC_006.py": {
            "label": "midday surplus floor 0.95",
            "midday_floor": 0.95,
        },
        "strategy_task2_mvbatchD_000.py": {
            "label": "grid 41",
            "soc_grid_points": 41,
        },
        "strategy_task2_mvbatchD_001.py": {
            "label": "grid 51",
            "soc_grid_points": 51,
        },
        "strategy_task2_mvbatchD_002.py": {
            "label": "horizon 72",
            "plan_horizon": 72,
        },
        "strategy_task2_mvbatchD_003.py": {
            "label": "horizon 120",
            "plan_horizon": 120,
        },
        "strategy_task2_mvcombo_000.py": {
            "label": "carbon=75 + trust 6",
            "carbon_price": 75.0,
            "forecast_trust_steps": 6,
        },
        "strategy_task2_mvcombo_001.py": {
            "label": "wear=25 + ramp=0.25",
            "battery_wear": 25.0,
            "ramp_charge": 0.25,
        },
        "strategy_task2_mvcombo_002.py": {
            "label": "dawn floor 0.55 + bias +60",
            "dawn_floor": 0.55,
            "dawn_bias_add_mw": 60.0,
        },
        "strategy_task2_mvcombo_003.py": {
            "label": "carbon=75 + wear=25 + demand factor=0.10",
            "carbon_price": 75.0,
            "battery_wear": 25.0,
            "future_demand_factor": 0.10,
        },
        "strategy_task2_mvfocus_000.py": {
            "label": "evening price bias -45",
            "evening_price_sub": 45.0,
        },
        "strategy_task2_mvfocus_001.py": {
            "label": "evening price bias -75",
            "evening_price_sub": 75.0,
        },
        "strategy_task2_mvfocus_002.py": {
            "label": "evening price bias -90",
            "evening_price_sub": 90.0,
        },
        "strategy_task2_mvfocus_003.py": {
            "label": "evening price -60 + dawn floor 0.55",
            "evening_price_sub": 60.0,
            "dawn_floor": 0.55,
        },
        "strategy_task2_mvfocus_004.py": {
            "label": "evening price -60 + dawn floor 0.52",
            "evening_price_sub": 60.0,
            "dawn_floor": 0.52,
        },
        "strategy_task2_mvfocus_005.py": {
            "label": "evening price -60 + dawn floor 0.58",
            "evening_price_sub": 60.0,
            "dawn_floor": 0.58,
        },
        "strategy_task2_mvfocus_006.py": {
            "label": "evening price -60 + carbon 100",
            "evening_price_sub": 60.0,
            "carbon_price": 100.0,
        },
        "strategy_task2_mvfocus_007.py": {
            "label": "evening price -60 + wear 25",
            "evening_price_sub": 60.0,
            "battery_wear": 25.0,
        },
        "strategy_task2_mvfocus_008.py": {
            "label": "evening price -60 + demand factor 0.10",
            "evening_price_sub": 60.0,
            "future_demand_factor": 0.10,
        },
        "strategy_task2_mvfocus_009.py": {
            "label": "evening price -60 + horizon 72",
            "evening_price_sub": 60.0,
            "plan_horizon": 72,
        },
    }

    for filename, params in variants.items():
        (OUT_DIR / filename).write_text(
            render_variant(source, **params),
            encoding="utf-8",
        )

    print(f"wrote {len(variants)} variants to {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
