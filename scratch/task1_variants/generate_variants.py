"""Generate local-only task 1 strategy variants for playtest sweeps.

The generated strategy files are experiment scaffolding. The submission
candidate remains ../../strategy_task1.py.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategy_task1.py"
OUT_DIR = Path(__file__).resolve().parent


def window_expr(windows: tuple[tuple[int, int], ...]) -> str:
    return " or ".join(
        f"{start} <= time_of_day <= {end}" for start, end in windows
    )


def render_variant(
    source: str,
    *,
    label: str,
    price_trigger: float | None = None,
    import_buffer: float | None = None,
    cap_factor: float | None = None,
    midday_soc: float | None = None,
    pre_peak_soc: float | None = None,
    peak_soc: float | None = None,
    default_soc: float | None = None,
    pre_peak_start: int | None = None,
    pre_peak_end: int | None = None,
    peak_start: int | None = None,
    peak_end: int | None = None,
    soc_weight: float | None = None,
    demand_weight: float | None = None,
    ramp_weight: float | None = None,
    battery_wear_weight: float | None = None,
    grid_charge: dict[str, object] | None = None,
    evening_shape: dict[str, float | int] | None = None,
    previous_day: dict[str, float] | None = None,
) -> str:
    text = source

    if price_trigger is not None:
        text = text.replace("price >= 430.0", f"price >= {price_trigger:.1f}")
    if import_buffer is not None:
        text = text.replace(
            "pre_action_import_mw >= costs.grid_max_import_mw - 10.0",
            f"pre_action_import_mw >= costs.grid_max_import_mw - {import_buffer:.1f}",
        )
    if cap_factor is not None:
        text = text.replace(
            "return self._clip(1.6 * sustainable_mw, 10.0, costs.max_inverter_mw)",
            f"return self._clip({cap_factor:.2f} * sustainable_mw, 10.0, costs.max_inverter_mw)",
        )

    if midday_soc is not None:
        text = text.replace("return 1.00", f"return {midday_soc:.2f}")
    if pre_peak_soc is not None:
        text = text.replace("return 0.45", f"return {pre_peak_soc:.2f}")
    if peak_soc is not None:
        text = text.replace("return 0.08", f"return {peak_soc:.2f}")
    if default_soc is not None:
        text = text.replace("return 0.05", f"return {default_soc:.2f}")
    if pre_peak_start is not None or pre_peak_end is not None:
        start = 63 if pre_peak_start is None else pre_peak_start
        end = 66 if pre_peak_end is None else pre_peak_end
        text = text.replace("if 63 <= time_of_day <= 66:", f"if {start} <= time_of_day <= {end}:")
    if peak_start is not None or peak_end is not None:
        start = 67 if peak_start is None else peak_start
        end = 86 if peak_end is None else peak_end
        text = text.replace("if 67 <= time_of_day <= 86:", f"if {start} <= time_of_day <= {end}:")

    if soc_weight is not None:
        text = text.replace(
            "self.soc_reserve_penalty = 1500000.0",
            f"self.soc_reserve_penalty = {soc_weight:.1f}",
        )
    if demand_weight is not None:
        text = text.replace("self.demand_charge = 1.0", f"self.demand_charge = {demand_weight:.2f}")
    if ramp_weight is not None:
        text = text.replace("self.ramp_charge = 1.0", f"self.ramp_charge = {ramp_weight:.2f}")
    if battery_wear_weight is not None:
        text = text.replace("self.battery_wear = 1.0", f"self.battery_wear = {battery_wear_weight:.2f}")

    if grid_charge is not None:
        windows = grid_charge.get("windows", ((0, 24),))
        if not isinstance(windows, tuple):
            raise TypeError("grid_charge windows must be a tuple of (start, end) tuples")
        price_max = float(grid_charge["price_max"])
        target_soc = float(grid_charge["target_soc"])
        max_mw = float(grid_charge["max_mw"])
        import_cap = float(grid_charge["import_cap"])
        candidate_block = f"""
        if self.should_grid_charge(state, soc):
            grid_charge_mw = self.grid_charge_power_limit(state, soc)
            if grid_charge_mw > 0.0:
                battery_options.update(
                    {{
                        -min(grid_charge_mw, 10.0),
                        -min(grid_charge_mw, 20.0),
                        -min(grid_charge_mw, 30.0),
                        -min(grid_charge_mw, 40.0),
                        -min(grid_charge_mw, 50.0),
                    }}
                )
"""
        text = text.replace(
            "\n        should_discharge = (\n",
            candidate_block + "\n        should_discharge = (\n",
        )
        method_block = f"""
    def grid_charge_target_soc(self, state: dict[str, Any]) -> float:
        time_of_day = int(state.get("time", 0)) % 96
        price = float(state.get("price", 0.0))
        solar = float(state.get("solar", 0.0))
        demand = float(state.get("demand", 0.0))

        if ({window_expr(windows)}) and price <= {price_max:.1f} and solar <= demand:
            return {target_soc:.2f}
        return 0.0

    def should_grid_charge(self, state: dict[str, Any], soc: float) -> bool:
        return soc < self.grid_charge_target_soc(state)

    def grid_charge_power_limit(self, state: dict[str, Any], soc: float) -> float:
        demand = float(state.get("demand", 0.0))
        solar = float(state.get("solar", 0.0))
        pre_action_import_mw = max(0.0, demand - solar)
        import_headroom_mw = max(0.0, {import_cap:.1f} - pre_action_import_mw)
        soc_headroom_mw = max(
            0.0,
            (
                self.grid_charge_target_soc(state) - soc
            )
            * self.ACTION_COSTS.battery_capacity_mwh
            / (self.ACTION_COSTS.charge_efficiency * self.ACTION_COSTS.dt_hours),
        )
        return min({max_mw:.1f}, import_headroom_mw, soc_headroom_mw)

"""
        text = text.replace(
            "\n    def desired_soc_floor(self, state: dict[str, Any]) -> float:\n",
            "\n" + method_block + "    def desired_soc_floor(self, state: dict[str, Any]) -> float:\n",
        )
        text = text.replace(
            "        if 40 <= time_of_day <= 62 and solar > demand:\n",
            "        grid_charge_target = self.grid_charge_target_soc(state)\n"
            "        if grid_charge_target > 0.0:\n"
            "            return grid_charge_target\n\n"
            "        if 40 <= time_of_day <= 62 and solar > demand:\n",
        )

    if evening_shape is not None:
        early_end = int(evening_shape["early_end"])
        early_soc = float(evening_shape["early_soc"])
        late_soc = float(evening_shape.get("late_soc", 0.08))
        text = text.replace(
            "        if 67 <= time_of_day <= 86:\n            return 0.08\n",
            f"        if 67 <= time_of_day <= {early_end}:\n            return {early_soc:.2f}\n"
            f"        if {early_end + 1} <= time_of_day <= 86:\n            return {late_soc:.2f}\n",
        )

    if previous_day is not None:
        trigger = float(previous_day.get("prior_price_trigger", 430.0))
        reserve_soc = float(previous_day.get("reserve_soc", 0.20))
        text = text.replace(
            "        self.last_objective_score: float = 0.0\n",
            "        self.last_objective_score: float = 0.0\n"
            "        self.history_by_tod: dict[int, list[dict[str, float]]] = {}\n",
        )
        text = text.replace(
            "        demand = float(state.get(\"demand\", 0.0))\n"
            "        solar = float(state.get(\"solar\", 0.0))\n\n"
            "        self.memory.last_time = int(state.get(\"time\", 0))\n"
            "        self.memory.last_soc = float(state.get(\"soc\", 0.0))\n"
            "        self.memory.last_net_grid_power_mw = demand - solar\n",
            "        demand = float(state.get(\"demand\", 0.0))\n"
            "        solar = float(state.get(\"solar\", 0.0))\n"
            "        current_time = int(state.get(\"time\", 0))\n"
            "        time_of_day = current_time % 96\n\n"
            "        if self.memory.last_time != current_time:\n"
            "            bucket = self.memory.history_by_tod.setdefault(time_of_day, [])\n"
            "            bucket.append(\n"
            "                {\n"
            "                    \"time\": float(current_time),\n"
            "                    \"price\": float(state.get(\"price\", 0.0)),\n"
            "                    \"net_load\": demand - solar,\n"
            "                }\n"
            "            )\n"
            "            if len(bucket) > 4:\n"
            "                del bucket[:-4]\n\n"
            "        self.memory.last_time = current_time\n"
            "        self.memory.last_soc = float(state.get(\"soc\", 0.0))\n"
            "        self.memory.last_net_grid_power_mw = demand - solar\n",
        )
        text = text.replace(
            "            price >= 430.0\n",
            f"            price >= 430.0\n            or self.prior_day_peak_hint(state) >= {trigger:.1f}\n",
        )
        method_block = f"""
    def prior_day_peak_hint(self, state: dict[str, Any]) -> float:
        time_of_day = int(state.get("time", 0)) % 96
        bucket = self.memory.history_by_tod.get(time_of_day, [])
        prior = [item for item in bucket if int(item.get("time", -1.0)) < int(state.get("time", 0))]
        if not prior:
            return 0.0
        return max(item["price"] for item in prior)

    def prior_day_future_peak_hint(self, state: dict[str, Any]) -> float:
        time_of_day = int(state.get("time", 0)) % 96
        best = 0.0
        for future_tod in range(max(67, time_of_day), 87):
            bucket = self.memory.history_by_tod.get(future_tod, [])
            for item in bucket:
                if int(item.get("time", -1.0)) < int(state.get("time", 0)):
                    best = max(best, item["price"])
        return best

"""
        text = text.replace(
            "\n    def desired_soc_floor(self, state: dict[str, Any]) -> float:\n",
            "\n" + method_block + "    def desired_soc_floor(self, state: dict[str, Any]) -> float:\n",
        )
        text = text.replace(
            "        if 40 <= time_of_day <= 62 and solar > demand:\n",
            f"        if 55 <= time_of_day <= 70 and self.prior_day_future_peak_hint(state) >= {trigger:.1f}:\n"
            f"            return {reserve_soc:.2f}\n\n"
            "        if 40 <= time_of_day <= 62 and solar > demand:\n",
        )

    header = f"# Local sweep variant: {label}\n# Generated by scratch/task1_variants/generate_variants.py\n\n"
    return header + text


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    variants = {
        "strategy_task1_mvbaseline.py": {"label": "current strategy_task1.py"},
        "strategy_task1_mvbatch1_variant000.py": {
            "label": "batch1 price=420 buffer=10 cap=1.60",
            "price_trigger": 420.0,
            "import_buffer": 10.0,
            "cap_factor": 1.60,
        },
        "strategy_task1_mvbatch1_variant001.py": {
            "label": "batch1 price=410 buffer=10 cap=1.60",
            "price_trigger": 410.0,
            "import_buffer": 10.0,
            "cap_factor": 1.60,
        },
        "strategy_task1_mvbatch1_variant002.py": {
            "label": "batch1 price=430 buffer=15 cap=1.60",
            "price_trigger": 430.0,
            "import_buffer": 15.0,
            "cap_factor": 1.60,
        },
        "strategy_task1_mvbatch1_variant003.py": {
            "label": "batch1 price=430 buffer=5 cap=1.60",
            "price_trigger": 430.0,
            "import_buffer": 5.0,
            "cap_factor": 1.60,
        },
        "strategy_task1_mvbatch1_variant004.py": {
            "label": "batch1 price=430 buffer=10 cap=1.40",
            "price_trigger": 430.0,
            "import_buffer": 10.0,
            "cap_factor": 1.40,
        },
        "strategy_task1_mvbatch1_variant005.py": {
            "label": "batch1 price=430 buffer=10 cap=1.80",
            "price_trigger": 430.0,
            "import_buffer": 10.0,
            "cap_factor": 1.80,
        },
        "strategy_task1_mvbatch2_variant000.py": {
            "label": "batch2 midday=1.00 pre=0.45 peak=0.08",
            "midday_soc": 1.00,
            "pre_peak_soc": 0.45,
            "peak_soc": 0.08,
        },
        "strategy_task1_mvbatch2_variant001.py": {
            "label": "batch2 midday=0.98 pre=0.35 peak=0.08",
            "midday_soc": 0.98,
            "pre_peak_soc": 0.35,
            "peak_soc": 0.08,
        },
        "strategy_task1_mvbatch2_variant002.py": {
            "label": "batch2 midday=0.98 pre=0.55 peak=0.08",
            "midday_soc": 0.98,
            "pre_peak_soc": 0.55,
            "peak_soc": 0.08,
        },
        "strategy_task1_mvbatch2_variant003.py": {
            "label": "batch2 midday=0.98 pre=0.45 peak=0.12",
            "midday_soc": 0.98,
            "pre_peak_soc": 0.45,
            "peak_soc": 0.12,
        },
        "strategy_task1_mvbatch2_variant004.py": {
            "label": "batch2 earlier pre-peak window",
            "pre_peak_start": 60,
            "pre_peak_end": 66,
        },
        "strategy_task1_mvbatch2_variant005.py": {
            "label": "batch2 shorter peak window",
            "peak_start": 68,
            "peak_end": 84,
        },
        "strategy_task1_mvbatch3_variant000.py": {
            "label": "batch3 soc_weight=1.0m",
            "soc_weight": 1000000.0,
        },
        "strategy_task1_mvbatch3_variant001.py": {
            "label": "batch3 soc_weight=2.0m",
            "soc_weight": 2000000.0,
        },
        "strategy_task1_mvbatch3_variant002.py": {
            "label": "batch3 demand_weight=1.25",
            "demand_weight": 1.25,
        },
        "strategy_task1_mvbatch3_variant003.py": {
            "label": "batch3 demand_weight=1.50",
            "demand_weight": 1.50,
        },
        "strategy_task1_mvbatch3_variant004.py": {
            "label": "batch3 ramp_weight=0.50",
            "ramp_weight": 0.50,
        },
        "strategy_task1_mvbatch3_variant005.py": {
            "label": "batch3 battery_wear_weight=0.50",
            "battery_wear_weight": 0.50,
        },
        "strategy_task1_mvcombo_variant000.py": {
            "label": "combo best batch2 + soc_weight=2.0m",
            "midday_soc": 1.00,
            "soc_weight": 2000000.0,
        },
        "strategy_task1_mvcombo_variant001.py": {
            "label": "combo best batch2 + battery_wear_weight=0.50",
            "midday_soc": 1.00,
            "battery_wear_weight": 0.50,
        },
        "strategy_task1_mvcombo_variant002.py": {
            "label": "combo best batch2 + soc_weight=2.0m + battery_wear_weight=0.50",
            "midday_soc": 1.00,
            "soc_weight": 2000000.0,
            "battery_wear_weight": 0.50,
        },
        "strategy_task1_mvbatch4_variant000.py": {
            "label": "batch4 overnight grid charge gentle",
            "grid_charge": {
                "windows": ((0, 24), (88, 95)),
                "price_max": 160.0,
                "target_soc": 0.30,
                "max_mw": 20.0,
                "import_cap": 90.0,
            },
        },
        "strategy_task1_mvbatch4_variant001.py": {
            "label": "batch4 overnight grid charge medium",
            "grid_charge": {
                "windows": ((0, 28), (88, 95)),
                "price_max": 180.0,
                "target_soc": 0.45,
                "max_mw": 25.0,
                "import_cap": 95.0,
            },
        },
        "strategy_task1_mvbatch4_variant002.py": {
            "label": "batch4 overnight grid charge assertive",
            "grid_charge": {
                "windows": ((0, 32), (88, 95)),
                "price_max": 210.0,
                "target_soc": 0.60,
                "max_mw": 30.0,
                "import_cap": 100.0,
            },
        },
        "strategy_task1_mvbatch4_variant003.py": {
            "label": "batch4 early morning grid charge",
            "grid_charge": {
                "windows": ((16, 36),),
                "price_max": 220.0,
                "target_soc": 0.50,
                "max_mw": 25.0,
                "import_cap": 95.0,
            },
        },
        "strategy_task1_mvbatch4_variant004.py": {
            "label": "batch4 low demand cap grid charge",
            "grid_charge": {
                "windows": ((0, 36), (88, 95)),
                "price_max": 200.0,
                "target_soc": 0.55,
                "max_mw": 20.0,
                "import_cap": 85.0,
            },
        },
        "strategy_task1_mvbatch4_variant005.py": {
            "label": "batch4 cheap-only high target",
            "grid_charge": {
                "windows": ((0, 28), (88, 95)),
                "price_max": 150.0,
                "target_soc": 0.70,
                "max_mw": 35.0,
                "import_cap": 95.0,
            },
        },
        "strategy_task1_mvbatch5_variant000.py": {
            "label": "batch5 hold 0.20 until tod 76",
            "evening_shape": {"early_end": 76, "early_soc": 0.20, "late_soc": 0.05},
        },
        "strategy_task1_mvbatch5_variant001.py": {
            "label": "batch5 hold 0.30 until tod 76",
            "evening_shape": {"early_end": 76, "early_soc": 0.30, "late_soc": 0.05},
        },
        "strategy_task1_mvbatch5_variant002.py": {
            "label": "batch5 hold 0.25 until tod 78",
            "evening_shape": {"early_end": 78, "early_soc": 0.25, "late_soc": 0.05},
        },
        "strategy_task1_mvbatch5_variant003.py": {
            "label": "batch5 hold 0.35 until tod 78",
            "evening_shape": {"early_end": 78, "early_soc": 0.35, "late_soc": 0.05},
        },
        "strategy_task1_mvbatch5_variant004.py": {
            "label": "batch5 hold 0.15 until tod 80",
            "evening_shape": {"early_end": 80, "early_soc": 0.15, "late_soc": 0.03},
        },
        "strategy_task1_mvbatch5_variant005.py": {
            "label": "batch5 hold 0.25 until tod 80",
            "evening_shape": {"early_end": 80, "early_soc": 0.25, "late_soc": 0.03},
        },
        "strategy_task1_mvbatch6_variant000.py": {
            "label": "batch6 prior-day discharge hint",
            "previous_day": {"prior_price_trigger": 430.0, "reserve_soc": 0.18},
        },
        "strategy_task1_mvbatch6_variant001.py": {
            "label": "batch6 prior-day lower trigger",
            "previous_day": {"prior_price_trigger": 410.0, "reserve_soc": 0.18},
        },
        "strategy_task1_mvbatch6_variant002.py": {
            "label": "batch6 prior-day reserve 0.25",
            "previous_day": {"prior_price_trigger": 430.0, "reserve_soc": 0.25},
        },
        "strategy_task1_mvbatch6_variant003.py": {
            "label": "batch6 prior-day aggressive reserve",
            "previous_day": {"prior_price_trigger": 410.0, "reserve_soc": 0.30},
        },
        "strategy_task1_mvcombo2_variant000.py": {
            "label": "combo2 grid medium + evening hold 0.20",
            "grid_charge": {
                "windows": ((0, 28), (88, 95)),
                "price_max": 180.0,
                "target_soc": 0.45,
                "max_mw": 25.0,
                "import_cap": 95.0,
            },
            "evening_shape": {"early_end": 76, "early_soc": 0.20, "late_soc": 0.05},
        },
        "strategy_task1_mvcombo2_variant001.py": {
            "label": "combo2 grid assertive + evening hold 0.20",
            "grid_charge": {
                "windows": ((0, 32), (88, 95)),
                "price_max": 210.0,
                "target_soc": 0.60,
                "max_mw": 30.0,
                "import_cap": 100.0,
            },
            "evening_shape": {"early_end": 76, "early_soc": 0.20, "late_soc": 0.05},
        },
        "strategy_task1_mvcombo2_variant002.py": {
            "label": "combo2 grid medium + prior-day",
            "grid_charge": {
                "windows": ((0, 28), (88, 95)),
                "price_max": 180.0,
                "target_soc": 0.45,
                "max_mw": 25.0,
                "import_cap": 95.0,
            },
            "previous_day": {"prior_price_trigger": 430.0, "reserve_soc": 0.18},
        },
        "strategy_task1_mvcombo2_variant003.py": {
            "label": "combo2 evening hold + prior-day",
            "evening_shape": {"early_end": 76, "early_soc": 0.20, "late_soc": 0.05},
            "previous_day": {"prior_price_trigger": 430.0, "reserve_soc": 0.18},
        },
        "strategy_task1_mvcombo2_variant004.py": {
            "label": "combo2 grid medium + evening hold + prior-day",
            "grid_charge": {
                "windows": ((0, 28), (88, 95)),
                "price_max": 180.0,
                "target_soc": 0.45,
                "max_mw": 25.0,
                "import_cap": 95.0,
            },
            "evening_shape": {"early_end": 76, "early_soc": 0.20, "late_soc": 0.05},
            "previous_day": {"prior_price_trigger": 430.0, "reserve_soc": 0.18},
        },
        "strategy_task1_mvcombo2_variant005.py": {
            "label": "combo2 grid assertive + evening hold + prior-day",
            "grid_charge": {
                "windows": ((0, 32), (88, 95)),
                "price_max": 210.0,
                "target_soc": 0.60,
                "max_mw": 30.0,
                "import_cap": 100.0,
            },
            "evening_shape": {"early_end": 76, "early_soc": 0.20, "late_soc": 0.05},
            "previous_day": {"prior_price_trigger": 430.0, "reserve_soc": 0.18},
        },
    }

    for filename, params in variants.items():
        path = OUT_DIR / filename
        path.write_text(render_variant(source, **params), encoding="utf-8")

    print(f"wrote {len(variants)} variants to {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
