"""Generate Task 2 variants with peak import as an explicit DP state."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategy_task2.py"
OUT_DIR = Path(__file__).resolve().parent


def replace_method(source: str, method_name: str, replacement: str) -> str:
    start = source.index(f"    def {method_name}(")
    next_def = source.index("\n    def _fallback_target_soc(", start)
    return source[:start] + replacement + source[next_def:]


def render_method(*, peak_points: int, demand_factor: float, diesel_cost: float) -> str:
    return f'''    def _plan_next_soc(
        self,
        *,
        soc: float,
        demand_plan: list[float],
        solar_plan: list[float],
        price_plan: list[float],
        peak_seen: float,
        grid_co2: float,
        previous_grid_mw: float | None = None,
    ) -> float:
        if np is None:
            return self._fallback_target_soc(
                soc=soc,
                demand_plan=demand_plan,
                solar_plan=solar_plan,
                price_plan=price_plan,
                peak_seen=peak_seen,
            )

        costs = self.ACTION_COSTS
        soc_levels = np.linspace(0.0, 1.0, self.SOC_GRID_POINTS)
        peak_levels = np.linspace(0.0, costs.grid_max_import_mw, {peak_points})
        n_soc = len(soc_levels)
        n_peak = len(peak_levels)
        horizon = len(demand_plan)

        soc_i = soc_levels[:, None]
        soc_j = soc_levels[None, :]
        flows = np.zeros((n_soc, n_soc))

        charge_mask = soc_j > soc_i
        flows[charge_mask] = -(
            (soc_j - soc_i)[charge_mask] * costs.battery_capacity_mwh
        ) / (costs.charge_efficiency * costs.dt_hours)

        discharge_mask = soc_j < soc_i
        flows[discharge_mask] = (
            (soc_i - soc_j)[discharge_mask]
            * costs.battery_capacity_mwh
            * costs.discharge_efficiency
        ) / costs.dt_hours

        valid_flow = (
            (flows >= -costs.max_inverter_mw)
            & (flows <= costs.max_inverter_mw)
        )
        battery_wear = (
            np.abs(flows) * costs.dt_hours * costs.battery_wear_per_mwh
        )

        value = np.zeros((horizon + 1, n_soc, n_peak))
        policy = np.zeros((horizon, n_soc, n_peak), dtype=int)

        for h in range(horizon - 1, -1, -1):
            demand = demand_plan[h]
            solar = solar_plan[h]
            price = price_plan[h]

            raw_grid = demand - solar - flows
            diesel = np.minimum(
                np.maximum(0.0, raw_grid - costs.grid_max_import_mw),
                costs.max_diesel_mw,
            )
            grid_after_diesel = raw_grid - diesel
            curtail = np.minimum(
                solar,
                np.maximum(0.0, -costs.grid_max_export_mw - grid_after_diesel),
            )
            net_grid = grid_after_diesel + curtail

            import_mwh = np.maximum(0.0, net_grid * costs.dt_hours)
            export_mwh = np.minimum(0.0, net_grid * costs.dt_hours)
            diesel_mwh = diesel * costs.dt_hours

            tariff = (
                import_mwh * price
                + export_mwh * costs.export_tariff_per_mwh
            )
            diesel_cost = diesel_mwh * {diesel_cost:.1f}
            carbon_cost = (
                import_mwh * grid_co2
                + diesel_mwh * costs.diesel_co2_kg_per_mwh
            ) * costs.carbon_price_per_kg

            ramp_charge = 0.0
            if h == 0 and previous_grid_mw is not None:
                ramp_charge = (
                    (net_grid - previous_grid_mw) ** 2
                ) * costs.ramp_charge_per_mw2

            base_cost = (
                tariff
                + diesel_cost
                + carbon_cost
                + battery_wear
                + ramp_charge
            )
            base_cost[~valid_flow] = np.inf

            for peak_i, peak_before in enumerate(peak_levels):
                new_peak = np.maximum(peak_before, np.maximum(0.0, net_grid))
                peak_j = np.searchsorted(peak_levels, new_peak, side="left")
                peak_j = np.clip(peak_j, 0, n_peak - 1)
                peak_after = peak_levels[peak_j]
                demand_charge = (
                    np.maximum(0.0, peak_after - peak_before)
                    * costs.demand_charge_per_mw
                    * {demand_factor:.2f}
                )

                future = value[h + 1][np.arange(n_soc)[None, :], peak_j]
                total = base_cost + demand_charge + future
                value[h, :, peak_i] = np.min(total, axis=1)
                policy[h, :, peak_i] = np.argmin(total, axis=1)

        current_soc_idx = int(np.argmin(np.abs(soc_levels - soc)))
        current_peak_idx = int(np.searchsorted(peak_levels, peak_seen, side="left"))
        current_peak_idx = max(0, min(n_peak - 1, current_peak_idx))
        next_soc_idx = int(policy[0, current_soc_idx, current_peak_idx])
        return float(soc_levels[next_soc_idx])

'''


def render_variant(
    source: str,
    *,
    label: str,
    peak_points: int,
    demand_factor: float,
    diesel_cost: float,
) -> str:
    text = replace_method(
        source,
        "_plan_next_soc",
        render_method(
            peak_points=peak_points,
            demand_factor=demand_factor,
            diesel_cost=diesel_cost,
        ),
    )
    header = (
        f"# Local sweep variant: peak-state DP {label}\n"
        "# Generated by scratch/task2_variants/generate_peak_state.py\n\n"
    )
    return header + text


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    variants = {
        "strategy_task2_mvpeakstate_000.py": {
            "label": "peak_points=25 demand=1.0 diesel=1000",
            "peak_points": 25,
            "demand_factor": 1.0,
            "diesel_cost": 1000.0,
        },
        "strategy_task2_mvpeakstate_001.py": {
            "label": "peak_points=49 demand=1.0 diesel=1000",
            "peak_points": 49,
            "demand_factor": 1.0,
            "diesel_cost": 1000.0,
        },
        "strategy_task2_mvpeakstate_002.py": {
            "label": "peak_points=25 demand=0.5 diesel=1000",
            "peak_points": 25,
            "demand_factor": 0.5,
            "diesel_cost": 1000.0,
        },
        "strategy_task2_mvpeakstate_003.py": {
            "label": "peak_points=49 demand=0.5 diesel=1000",
            "peak_points": 49,
            "demand_factor": 0.5,
            "diesel_cost": 1000.0,
        },
        "strategy_task2_mvpeakstate_004.py": {
            "label": "peak_points=25 demand=1.5 diesel=1000",
            "peak_points": 25,
            "demand_factor": 1.5,
            "diesel_cost": 1000.0,
        },
        "strategy_task2_mvpeakstate_005.py": {
            "label": "peak_points=25 demand=1.0 diesel=800",
            "peak_points": 25,
            "demand_factor": 1.0,
            "diesel_cost": 800.0,
        },
        "strategy_task2_mvpeakstate_006.py": {
            "label": "peak_points=25 demand=1.0 diesel=1200",
            "peak_points": 25,
            "demand_factor": 1.0,
            "diesel_cost": 1200.0,
        },
        "strategy_task2_mvpeakstate_007.py": {
            "label": "peak_points=61 demand=1.0 diesel=1000",
            "peak_points": 61,
            "demand_factor": 1.0,
            "diesel_cost": 1000.0,
        },
    }

    for filename, params in variants.items():
        (OUT_DIR / filename).write_text(
            render_variant(source, **params),
            encoding="utf-8",
        )

    print(f"wrote {len(variants)} peak-state variants to {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
