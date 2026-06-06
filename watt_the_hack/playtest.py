"""Local playtest CLI for Watt The Hack controllers.

Run a controller against any scenario without touching the cloud admin
server. Mirrors the cloud evaluator's contract (``Strategy`` class with
``plan``/``replan``/``step``, or a ``controller`` function) so a local
green run translates directly to a cloud submission.

Usage::

    python -m watt_the_hack.playtest path/to/my_controller.py --scenario duck_curve
    python -m watt_the_hack.playtest --list-scenarios

Per-run outputs land in ``runs/<scenario_id>_<UTC timestamp>/``:
    metrics.json   final cost summary + breakdown by component
    steps.csv      one row per simulation step (15 minutes each)
    meta.json      scenario + controller info (for reproducibility)
    soc.png        SOC trajectory (skipped if matplotlib missing)
    cost.png       cumulative cost over time
    action.png     dispatch overlay (battery, diesel, curtailment)

Controllers may live anywhere on disk; the file's parent directory is
added to ``sys.path`` so co-located helpers (``utils.py``, etc.) import
cleanly.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from watt_the_hack.data_loaders.scenarios import list_scenarios
from watt_the_hack.engine.engine import Engine
from watt_the_hack.simulation.boot import (
    ScenarioNotFound,
    boot_scenario,
    scenario_steps,
)
from watt_the_hack.simulation.runner import run_strategy
from watt_the_hack.simulation.strategy import (
    ZERO_ACTION,
    resolve_strategy_from_path,
)


def run_playtest(
    controller_path: Path,
    scenario_id: str,
    out_dir: Path | None = None,
    plots: bool = True,
    max_steps: int | None = None,
    verbose: bool = True,
    open_report: bool = False,
) -> dict:
    """Run one scenario end-to-end against a controller file. Returns a
    result dict and writes artifacts to ``out_dir`` (created if missing).

    Set ``plots=False`` to skip PNG generation (no matplotlib needed).
    """
    controller_path = Path(controller_path).resolve()

    try:
        engine, state, spec = boot_scenario(scenario_id)
    except ScenarioNotFound as exc:
        raise SystemExit(
            f"{exc} Run with --list-scenarios to see available options."
        ) from exc

    strategy = resolve_strategy_from_path(controller_path, name=controller_path.stem)

    total_steps = scenario_steps(state)
    if max_steps is not None:
        total_steps = min(total_steps, int(max_steps))

    rows: list[dict[str, Any]] = []

    def _on_step(i, view, action, outputs, state_after):
        rows.append(_per_step_row(i, engine, view, action, outputs, state_after, _breakdown))

    def _on_error(phase, step, exc):
        prefix = f"[{phase}" + (f" @ step {step}]" if step >= 0 else "]")
        print(f"  {prefix} raised: {exc}", file=sys.stderr)
        if phase != "step":
            traceback.print_exc(file=sys.stderr)

    if verbose:
        print(
            f"Scenario : {spec.get('id')} - {spec.get('title', '')}".rstrip()
        )
        print(f"Steps    : {total_steps}  (dt = {engine.config.dt_hours}h)")
        print(f"Controller: {controller_path}  (kind={strategy.kind})")

    # _breakdown is populated incrementally by the engine's per-step cost
    # breakdown so each CSV row carries the running cumulative cost.
    _breakdown: dict[str, float] = {}
    started = time.perf_counter()

    def _accumulate_breakdown(i, view, action, outputs, state_after):
        for k, v in outputs.get("cost_breakdown", {}).items():
            if k == "total":  # already the sum of the components — don't double-count cum_cost
                continue
            _breakdown[k] = _breakdown.get(k, 0.0) + float(v)
        _on_step(i, view, action, outputs, state_after)

    result = run_strategy(
        engine,
        state,
        strategy,
        total_steps,
        on_step=_accumulate_breakdown,
        on_error=_on_error,
    )
    wall = time.perf_counter() - started

    summary = result["metrics"]
    breakdown = result["cost_breakdown"]

    if out_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("runs") / f"{spec.get('id', 'scenario')}_{ts}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_metrics(out_dir / "metrics.json", summary, breakdown)
    _write_steps_csv(out_dir / "steps.csv", rows)
    _write_meta(
        out_dir / "meta.json",
        scenario=spec,
        controller_path=controller_path,
        kind=strategy.kind,
        wall_seconds=wall,
        total_steps=total_steps,
    )
    if plots:
        _maybe_write_plots(out_dir, rows, breakdown)
    report_path = _write_report_html(
        out_dir / "report.html",
        summary=summary,
        breakdown=breakdown,
        rows=rows,
        scenario=spec,
        controller_path=controller_path,
    )

    if verbose:
        _print_summary(
            summary, breakdown, wall, out_dir, report_path,
            scenario_id=spec.get("id", ""),
            baselines=(spec.get("scoring") or {}).get("baselines") or {},
        )

    if open_report:
        _open_report(report_path)

    return {
        "metrics": summary,
        "breakdown": breakdown,
        "rows": rows,
        "out_dir": str(out_dir),
        "report_path": str(report_path),
        "wall_seconds": wall,
    }


def _per_step_row(
    step: int,
    engine: Engine,
    view: dict,
    action: dict,
    outputs: dict,
    state: dict,
    breakdown_cum: dict,
) -> dict:
    return {
        "step": step,
        "time_hours": round(step * engine.config.dt_hours, 4),
        "demand": float(view.get("demand", 0.0)),
        "solar": float(view.get("solar", 0.0)),
        "price": float(view.get("price", 0.0)),
        "soc": float(state.get("soc", 0.0)),
        "battery_flow_mw": float(action.get("battery_flow_mw", 0.0)),
        "emergency_generator_mw": float(action.get("emergency_generator", 0.0)),
        "curtail_solar_mw": float(action.get("curtail_solar", 0.0)),
        "fcas_reserve_mw": float(action.get("fcas_reserve_mw", 0.0)),
        "net_grid_power_mw": float(outputs.get("net_grid_power", 0.0)),
        "unmet_demand_mw": float(outputs.get("unmet_demand", 0.0)),
        "overvoltage_mw": float(outputs.get("overvoltage_mw", 0.0)),
        "step_cost": float(outputs.get("cost", 0.0)),
        "cum_cost": float(sum(breakdown_cum.values())),
    }


def _write_metrics(path: Path, summary: dict, breakdown: dict) -> None:
    payload = {
        "summary": summary,
        "cost_breakdown": {k: round(v, 4) for k, v in breakdown.items()},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_steps_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_meta(
    path: Path,
    *,
    scenario: dict,
    controller_path: Path,
    kind: str,
    wall_seconds: float,
    total_steps: int,
) -> None:
    meta = {
        "scenario_id": scenario.get("id"),
        "scenario_title": scenario.get("title"),
        "scenario_mechanics": [m for m in scenario.get("features", {}).items()] if scenario.get("features") else "all enabled",
        "controller_path": str(controller_path),
        "controller_kind": kind,
        "total_steps": total_steps,
        "wall_seconds": round(wall_seconds, 3),
        "engine_version": "0.1.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _maybe_write_plots(out_dir: Path, rows: list[dict], breakdown: dict) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: E402
    except ImportError:
        print(
            "  [plots] matplotlib not installed; skipping PNGs. "
            "`pip install matplotlib` to enable.",
            file=sys.stderr,
        )
        return
    if not rows:
        return

    t = [r["time_hours"] for r in rows]

    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.plot(t, [r["soc"] for r in rows], color="tab:blue")
    ax.set_xlabel("hours")
    ax.set_ylabel("SOC (0–1)")
    ax.set_title("Battery state of charge")
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "soc.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.plot(t, [r["cum_cost"] for r in rows], color="tab:red")
    ax.set_xlabel("hours")
    ax.set_ylabel("cumulative cost ($)")
    ax.set_title(f"Total cost = ${rows[-1]['cum_cost']:.2f}")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "cost.png", dpi=120)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    ax = axes[0]
    ax.plot(t, [r["demand"] for r in rows], label="demand", color="tab:orange")
    ax.plot(t, [r["solar"] for r in rows], label="solar", color="tab:green")
    ax.plot(t, [r["net_grid_power_mw"] for r in rows], label="net grid (+import / -export)", color="tab:gray")
    ax.set_ylabel("MW")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax = axes[1]
    ax.plot(t, [r["battery_flow_mw"] for r in rows], label="battery flow (+disch/-ch)", color="tab:blue")
    ax.plot(t, [r["emergency_generator_mw"] for r in rows], label="diesel", color="tab:red")
    ax.plot(t, [r["curtail_solar_mw"] for r in rows], label="curtail", color="tab:green", linestyle="--")
    ax.set_xlabel("hours")
    ax.set_ylabel("MW")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.suptitle("Dispatch overview")
    fig.tight_layout()
    fig.savefig(out_dir / "action.png", dpi=120)
    plt.close(fig)


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_num(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}"


def _td(value: Any) -> str:
    return f"<td>{html.escape(str(value))}</td>"


def _diagnostic_hints(breakdown: dict) -> list[str]:
    costs = {k: v for k, v in breakdown.items() if k != "total"}
    top = [k for k, v in sorted(costs.items(), key=lambda kv: -abs(kv[1]))[:4] if abs(v) > 1e-9]
    hints: list[str] = []
    if "overvoltage_penalty" in top:
        hints.append("Overvoltage is expensive here: inspect high-solar periods where net grid power is negative. Better controllers should charge the battery or curtail solar before exporting too much.")
    if "tariff_import" in top or "demand_charge" in top:
        hints.append("Import and demand charges are driving cost: look for evening or morning peaks where the battery could discharge to reduce grid import.")
    if "ramp_charge" in top:
        hints.append("Ramp charges are material: avoid abrupt battery or diesel changes unless they prevent a larger penalty.")
    if "battery_wear" in top:
        hints.append("Battery wear is material: cycling needs to be reserved for high-value periods, not every small price movement.")
    if "blackout_penalty" in top:
        hints.append("Blackout penalties dominate: the controller is failing the physical supply constraint before it is optimising cost.")
    if "fcas_revenue" in top:
        hints.append("FCAS revenue is affecting score: check whether reserve commitments are crowding out battery dispatch when the grid needs energy.")
    return hints or ["No single diagnostic dominates. Compare the worst timesteps and action stats against the scenario intent."]


# Reference costs ($, raw) for PUBLIC scenarios so a local run self-anchors
# ("is my $912k any good?"). FLOOR ONLY — do_nothing (zero action) and naive
# (the simple baseline). We deliberately do NOT ship the optimum: the objective
# is already a gradient an agent can descend, so a visible ceiling adds no skill,
# only a number to grind toward. Extend as scenarios are measured.
PUBLIC_BASELINES: dict[str, dict[str, float]] = {
    "duck_curve": {"do_nothing": 1_959_201.0, "naive": 973_334.0},
}


def _anchor_rungs(scenario_id: str, score: float) -> list[tuple[str, float, str]] | None:
    """Ladder rows (label, cost, note), worst cost first, with the runner's own
    score slotted in. Anchored to the FLOOR (do-nothing, naive) only, never the
    optimum — so neither a participant nor an iterating agent gets a ceiling to
    grind toward. None if the scenario has no published baseline."""
    base = PUBLIC_BASELINES.get(scenario_id)
    if not base:
        return None
    naive = base["naive"]
    if score < naive:
        you_note = f"beating the naive baseline by {(naive - score) / naive * 100:.0f}%"
    else:
        you_note = "above the naive baseline — keep going"
    rungs = [
        ("do nothing", base["do_nothing"], ""),
        ("naive baseline", naive, ""),
        ("you", score, you_note),
    ]
    rungs.sort(key=lambda r: -r[1])
    return rungs


def _biggest_lever(breakdown: dict, final_score: float) -> tuple[str, float, float, str] | None:
    """The single dominant cost component, its share of total, and a one-line hint."""
    costs = {k: v for k, v in breakdown.items() if k != "total" and abs(v) > 1e-9}
    if not costs:
        return None
    comp, val = max(costs.items(), key=lambda kv: abs(kv[1]))
    share = abs(val) / final_score * 100 if final_score else 0.0
    return comp, val, share, _diagnostic_hints({comp: val})[0]


def _hour_window(rows_subset: list[dict]) -> str:
    """Compact elapsed-hour range for a subset of steps, e.g. 'h10-14'."""
    if not rows_subset:
        return ""
    lo = min(r["time_hours"] for r in rows_subset)
    hi = max(r["time_hours"] for r in rows_subset)
    return f"h{lo:.0f}–{hi:.0f}"


def _opportunities(rows: list[dict], breakdown: dict, dt_hours: float, final: float) -> list[dict]:
    """Specific, data-derived "you left money here" findings, ranked by severity.
    Everything is read off the per-step trace — no re-simulation, negligible
    compute. Each finding names what happened, when, how much, and the fix."""
    bd = {k: v for k, v in breakdown.items() if k != "total"}
    prices = sorted(r["price"] for r in rows)
    p_hi = prices[int(len(prices) * 0.75)] if prices else 0.0  # top-quartile price
    out: list[dict] = []

    unmet = [r for r in rows if r["unmet_demand_mw"] > 1e-6]
    if unmet:
        mwh = sum(r["unmet_demand_mw"] for r in unmet) * dt_hours
        out.append(dict(sev="high", icon="⛔", title="Unmet demand — the grid went dark",
            detail=f"{len(unmet)} steps ({_hour_window(unmet)}) failed to meet demand, {mwh:.1f} MWh short. "
                   "This is a physical supply failure and swamps every other cost — fix it before tuning "
                   "anything else: hold more reserve, free up diesel, or import ahead of the shortfall."))

    ov = [r for r in rows if r["overvoltage_mw"] > 1e-6]
    if ov:
        mwh = sum(r["overvoltage_mw"] for r in ov) * dt_hours
        cost = bd.get("overvoltage_penalty", 0.0)
        out.append(dict(sev="high" if cost > 0.1 * abs(final or 1.0) else "med",
            icon="⚡", title="Over-exporting solar (overvoltage)",
            detail=f"{len(ov)} steps ({_hour_window(ov)}) pushed {mwh:.1f} MWh past the export cap"
                   + (f", costing {_fmt_money(cost)}" if cost else "")
                   + ". Charge the battery or curtail during the midday solar surge instead of dumping it on the grid."))

    cwr = [r for r in rows if r["curtail_solar_mw"] > 1e-6 and r["soc"] < 0.85]
    if cwr:
        mwh = sum(r["curtail_solar_mw"] for r in cwr) * dt_hours
        out.append(dict(sev="med", icon="\U0001f31e", title="Throwing away storable solar",
            detail=f"You curtailed {mwh:.1f} MWh of free solar across {len(cwr)} steps while the battery was "
                   "under 85% full. Store it instead and spend it at the evening peak."))

    idle = [r for r in rows if p_hi > 0 and r["price"] >= p_hi and abs(r["battery_flow_mw"]) < 1.0 and r["soc"] > 0.25]
    if idle:
        out.append(dict(sev="med", icon="\U0001f50b", title="Battery idle while power was expensive",
            detail=f"On {len(idle)} of the priciest steps ({_hour_window(idle)}) the battery sat still with charge "
                   "to spare (SOC > 25%). Discharging into those peaks is the cheapest win on the board."))

    chg = [r for r in rows if p_hi > 0 and r["battery_flow_mw"] < -1.0 and r["price"] >= p_hi]
    if chg:
        mwh = sum(-r["battery_flow_mw"] for r in chg) * dt_hours
        out.append(dict(sev="med", icon="\U0001f4b8", title="Charging at the worst possible price",
            detail=f"You drew {mwh:.1f} MWh into the battery during top-quartile prices on {len(chg)} steps. "
                   "Charge in the cheap, sunny middle of the day instead."))

    dz = [r for r in rows if r["emergency_generator_mw"] > 1e-6 and r["soc"] > 0.25]
    if dz:
        mwh = sum(r["emergency_generator_mw"] for r in dz) * dt_hours
        out.append(dict(sev="low", icon="\U0001f6e2️", title="Burning diesel with charge in the battery",
            detail=f"{mwh:.1f} MWh of diesel across {len(dz)} steps while SOC was above 25%. Diesel is dear and "
                   "carbon-taxed — lean on the battery before the generator."))

    if not out:
        out.append(dict(sev="low", icon="✅", title="No obvious leaks",
            detail="No blackouts, no overvoltage, no wasteful curtailment or peak-price charging. From here it's "
                   "fine-tuning — read the cost breakdown and the period table below for the marginal gains."))

    sev_order = {"high": 0, "med": 1, "low": 2}
    out.sort(key=lambda d: sev_order[d["sev"]])
    return out[:6]


def _period_breakdown(rows: list[dict], dt_hours: float) -> list[dict]:
    """Fold the run into time-of-day bands (hour mod 24, so multi-day aggregates)
    to surface the temporal story — where SOC ran out, where import bit hardest."""
    bands = [
        ("Overnight", 0, 6), ("Morning ramp", 6, 10), ("Midday solar", 10, 15),
        ("Afternoon", 15, 17), ("Evening peak", 17, 22), ("Late evening", 22, 24),
    ]
    out: list[dict] = []
    for name, lo, hi in bands:
        rs = [r for r in rows if lo <= (r["time_hours"] % 24) < hi]
        if not rs:
            continue
        out.append(dict(
            name=name,
            avg_soc=sum(r["soc"] for r in rs) / len(rs),
            avg_price=sum(r["price"] for r in rs) / len(rs),
            import_mwh=sum(max(0.0, r["net_grid_power_mw"]) for r in rs) * dt_hours,
            curtail_mwh=sum(r["curtail_solar_mw"] for r in rs) * dt_hours,
            cost=sum(r["step_cost"] for r in rs),
            ov=sum(1 for r in rs if r["overvoltage_mw"] > 1e-6),
            unmet=sum(1 for r in rs if r["unmet_demand_mw"] > 1e-6),
        ))
    return out


def _sparkline(values: list[float], color: str = "#2f6df6", fill: bool = False,
               w: int = 150, h: int = 34) -> str:
    """A tiny inline SVG line chart from a series — zero dependencies, always
    rendered (even under --no-plots)."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n, pad = len(values), 3

    def px(i: int) -> float:
        return pad + i * (w - 2 * pad) / max(n - 1, 1)

    def py(v: float) -> float:
        return pad + (h - 2 * pad) * (1 - (v - lo) / span)

    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(values))
    area = (f'<polygon points="{pad},{h - pad} {pts} {w - pad},{h - pad}" fill="{color}" opacity="0.12"/>'
            if fill else "")
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="spark">{area}'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


def _write_report_html(
    path: Path,
    *,
    summary: dict,
    breakdown: dict,
    rows: list[dict],
    scenario: dict,
    controller_path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sid = str(scenario.get("id", ""))
    final = summary["final_score"]
    dt_hours = rows[1]["time_hours"] - rows[0]["time_hours"] if len(rows) > 1 else 0.25
    worst_steps = sorted(rows, key=lambda r: r["step_cost"], reverse=True)[:10]
    overvoltage_steps = [r for r in rows if abs(r["overvoltage_mw"]) > 1e-9]
    unmet_steps = [r for r in rows if abs(r["unmet_demand_mw"]) > 1e-9]
    imports = [max(0.0, r["net_grid_power_mw"]) for r in rows]
    battery = [r["battery_flow_mw"] for r in rows]
    diesel = [r["emergency_generator_mw"] for r in rows]
    curtail = [r["curtail_solar_mw"] for r in rows]
    throughput = sum(abs(v) for v in battery) * dt_hours

    cost_rows = "\n".join(
        "<tr>" + _td(k) + _td(_fmt_money(v))
        + _td(f"{(v / final * 100):.1f}%" if final else "n/a") + "</tr>"
        for k, v in sorted(breakdown.items(), key=lambda kv: -abs(kv[1]))
        if k != "total"
    )
    worst_rows = "\n".join(
        "<tr>"
        + _td(r["step"]) + _td(_fmt_num(r["time_hours"], 2))
        + _td(_fmt_money(r["step_cost"])) + _td(_fmt_money(r["cum_cost"]))
        + _td(_fmt_num(r["demand"], 1)) + _td(_fmt_num(r["solar"], 1))
        + _td(_fmt_num(r["net_grid_power_mw"], 1)) + _td(_fmt_num(r["soc"], 2))
        + _td(_fmt_num(r["battery_flow_mw"], 1)) + _td(_fmt_num(r["overvoltage_mw"], 1))
        + _td(_fmt_num(r["unmet_demand_mw"], 1)) + "</tr>"
        for r in worst_steps
    )

    # Where you're losing money — ranked, data-derived, no re-simulation.
    sev_bg = {"high": ("#fdecec", "#d64545"), "med": ("#fdf5e6", "#c9881f"), "low": ("#eaf7f0", "#2f9e6f")}
    opp_html = "".join(
        f'<div class="opp" style="background:{sev_bg[o["sev"]][0]};border-left-color:{sev_bg[o["sev"]][1]}">'
        f'<div class="opp-t"><span class="opp-i">{o["icon"]}</span>{html.escape(o["title"])}</div>'
        f'<div class="opp-d">{html.escape(o["detail"])}</div></div>'
        for o in _opportunities(rows, breakdown, dt_hours, final)
    )

    # Floor-only ladder (no optimum shown).
    anchor_rungs = _anchor_rungs(sid, final)
    if anchor_rungs:
        ladder = "\n".join(
            "<tr>"
            + f'<td>{"&#9654; " if label == "you" else ""}{html.escape(label)}</td>'
            + _td(_fmt_money(cost)) + f"<td>{html.escape(note)}</td></tr>"
            for label, cost, note in anchor_rungs
        )
        anchor_card = (
            '<section class="card"><h2>How you&#39;re doing</h2>'
            '<p class="hint">Raw cost against the baselines you should beat (lower wins). '
            'No optimum is shown on purpose &mdash; drive the cost down and chase the levers above.</p>'
            f'<table><tr><th>controller</th><th>cost</th><th>vs baseline</th></tr>{ladder}</table></section>'
        )
        naive = PUBLIC_BASELINES[sid]["naive"]
        vs_naive = f"{(naive - final) / naive * 100:+.0f}%"
    else:
        anchor_card = ""
        vs_naive = "&mdash;"

    # Verdict pill.
    if summary.get("unmet_demand_total", 0.0) > 1e-6:
        verdict_txt, verdict_col = "Blackout — fix supply first", "#d64545"
    elif anchor_rungs and final < PUBLIC_BASELINES[sid]["naive"]:
        verdict_txt, verdict_col = "Beating the baseline", "#2f9e6f"
    elif anchor_rungs:
        verdict_txt, verdict_col = "Below the baseline", "#c9881f"
    else:
        verdict_txt, verdict_col = "Run complete", "#5b6475"

    # KPI strip.
    blackout_col = "#d64545" if summary["unmet_demand_total"] > 1e-6 else "#141b2d"
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-l">{lab}</div>'
        f'<div class="kpi-v" style="color:{col}">{val}</div><div class="kpi-s">{sub}</div></div>'
        for lab, val, sub, col in [
            ("Final cost", _fmt_money(final), "lower wins", "#141b2d"),
            ("vs naive", vs_naive, "negative = cheaper", "#141b2d"),
            ("Unmet demand", f'{summary["unmet_demand_total"]:.1f} MWh', "0 = no blackout", blackout_col),
            ("Renewable", f'{summary["renewable_ratio"]:.2f}', "share served clean", "#141b2d"),
            ("Battery throughput", f"{throughput:.0f} MWh", "wear scales with this", "#141b2d"),
            ("Peak import", f"{max(imports, default=0.0):.0f} MW", "demand-charge driver", "#141b2d"),
        ]
    )

    # Trends — zero-dependency inline SVG sparklines (render even with --no-plots).
    spark_html = "".join(
        f'<div class="sp"><div class="sp-l">{lab}</div>{_sparkline(series, col, fill=fl)}'
        f'<div class="sp-s">{sub}</div></div>'
        for lab, series, col, fl, sub in [
            ("Battery SOC", [r["soc"] for r in rows], "#2f6df6", True, "0 → 1"),
            ("Price", [r["price"] for r in rows], "#c9881f", False, "$/MWh"),
            ("Net grid", [r["net_grid_power_mw"] for r in rows], "#6b7280", False, "+imp / -exp MW"),
            ("Cumulative cost", [r["cum_cost"] for r in rows], "#d64545", True, "$ run total"),
        ]
    )

    # By time of day.
    period_rows = "\n".join(
        "<tr>"
        + _td(p["name"]) + _td(_fmt_num(p["avg_soc"], 2)) + _td(_fmt_num(p["avg_price"], 0))
        + _td(_fmt_num(p["import_mwh"], 1)) + _td(_fmt_num(p["curtail_mwh"], 1)) + _td(_fmt_money(p["cost"]))
        + _td(f'{p["unmet"]} unmet' if p["unmet"] else (f'{p["ov"]} overvolt' if p["ov"] else "clean"))
        + "</tr>"
        for p in _period_breakdown(rows, dt_hours)
    )

    plot_cards = []
    for filename, title in [("action.png", "Dispatch"), ("cost.png", "Cumulative Cost"), ("soc.png", "Battery SOC")]:
        if (path.parent / filename).exists():
            plot_cards.append(f'<section class="card"><h2>{title}</h2><img src="{filename}" alt="{title}"></section>')
    plots_block = "".join(plot_cards)

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Watt The Hack — Playtest Report</title>
  <style>
    :root {{ --ink:#141b2d; --mut:#5b6475; --line:#e6e9f2; --bg:#f4f6fb; --card:#fff; }}
    * {{ box-sizing:border-box; }}
    body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
            margin:0; color:var(--ink); background:var(--bg); line-height:1.5; }}
    .wrap {{ max-width:1040px; margin:0 auto; padding:24px 20px 56px; }}
    h1 {{ font-size:20px; margin:0; }}
    h2 {{ font-size:15px; margin:0 0 12px; }}
    .topbar {{ background:linear-gradient(135deg,#1b2440,#2f3e6b); color:#fff; border-radius:16px;
               padding:22px 24px; margin-bottom:18px; box-shadow:0 6px 24px rgba(20,30,60,.18); }}
    .topbar .sub {{ color:#c5cde8; font-size:13px; margin-top:4px; }}
    .topbar .big {{ font-size:40px; font-weight:800; margin:14px 0 10px; letter-spacing:-.02em; }}
    .verdict {{ display:inline-block; padding:4px 12px; border-radius:999px; font-size:12px; font-weight:700; color:#fff; }}
    .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:18px; }}
    .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:13px 15px; }}
    .kpi-l {{ color:var(--mut); font-size:12px; }}
    .kpi-v {{ font-size:22px; font-weight:750; margin:3px 0 1px; letter-spacing:-.01em; }}
    .kpi-s {{ color:var(--mut); font-size:11px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px;
             margin:0 0 16px; box-shadow:0 1px 2px rgba(0,0,0,.03); }}
    .hint {{ color:var(--mut); font-size:12px; margin:0 0 10px; }}
    .opp {{ border-left:4px solid; border-radius:8px; padding:11px 14px; margin-bottom:9px; }}
    .opp:last-child {{ margin-bottom:0; }}
    .opp-t {{ font-weight:700; font-size:14px; }}
    .opp-i {{ margin-right:7px; }}
    .opp-d {{ color:#3b4254; font-size:13px; margin-top:2px; }}
    .two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; align-items:start; }}
    .sparks {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:14px; }}
    .sp-l {{ font-size:12px; color:var(--mut); margin-bottom:3px; }}
    .sp-s {{ font-size:11px; color:var(--mut); margin-top:2px; }}
    svg.spark {{ width:100%; height:34px; display:block; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid var(--line); padding:7px 8px; text-align:right; }}
    th {{ color:var(--mut); font-weight:600; }}
    th:first-child, td:first-child {{ text-align:left; }}
    img {{ max-width:100%; border:1px solid var(--line); border-radius:8px; }}
    code {{ background:#eef1f7; padding:2px 5px; border-radius:5px; font-size:12px; }}
    a {{ color:#2f6df6; }}
    @media (max-width:720px) {{ .two {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <h1>{html.escape(str(scenario.get("title", "")) or sid)}</h1>
      <div class="sub">{html.escape(sid)} &middot; <code>{html.escape(controller_path.name)}</code> &middot; {len(rows)} steps &times; {dt_hours:.2f} h</div>
      <div class="big">{_fmt_money(final)}</div>
      <span class="verdict" style="background:{verdict_col}">{html.escape(verdict_txt)}</span>
    </div>

    <div class="kpis">{kpi_html}</div>

    <section class="card">
      <h2>Where you're losing money</h2>
      {opp_html}
    </section>

    <div class="two">
      {anchor_card}
      <section class="card"><h2>Trends</h2><div class="sparks">{spark_html}</div></section>
    </div>

    {plots_block}

    <section class="card">
      <h2>By time of day</h2>
      <table>
        <tr><th>Period</th><th>Avg SOC</th><th>Avg $/MWh</th><th>Import MWh</th><th>Curtail MWh</th><th>Cost</th><th>Issue</th></tr>
        {period_rows}
      </table>
    </section>

    <section class="card">
      <h2>Cost breakdown</h2>
      <table><tr><th>Component</th><th>Cost</th><th>Share</th></tr>{cost_rows}</table>
    </section>

    <div class="two">
      <section class="card"><h2>Grid violations</h2>
        <table><tr><th>Signal</th><th>Steps</th><th>Max MW</th></tr>
          <tr>{_td("overvoltage")}{_td(len(overvoltage_steps))}{_td(_fmt_num(max([r["overvoltage_mw"] for r in rows], default=0.0), 1))}</tr>
          <tr>{_td("unmet demand")}{_td(len(unmet_steps))}{_td(_fmt_num(max([r["unmet_demand_mw"] for r in rows], default=0.0), 1))}</tr>
        </table>
      </section>
      <section class="card"><h2>Action stats</h2>
        <table><tr><th>Signal</th><th>Min</th><th>Max</th><th>Total</th></tr>
          <tr>{_td("battery MW")}{_td(_fmt_num(min(battery, default=0.0), 1))}{_td(_fmt_num(max(battery, default=0.0), 1))}{_td(_fmt_num(throughput, 1) + " MWh")}</tr>
          <tr>{_td("diesel MW")}{_td(_fmt_num(min(diesel, default=0.0), 1))}{_td(_fmt_num(max(diesel, default=0.0), 1))}{_td(_fmt_num(sum(diesel) * dt_hours, 1) + " MWh")}</tr>
          <tr>{_td("curtail MW")}{_td(_fmt_num(min(curtail, default=0.0), 1))}{_td(_fmt_num(max(curtail, default=0.0), 1))}{_td(_fmt_num(sum(curtail) * dt_hours, 1) + " MWh")}</tr>
        </table>
      </section>
    </div>

    <section class="card">
      <h2>Worst timesteps</h2>
      <table>
        <tr><th>Step</th><th>Hour</th><th>Step Cost</th><th>Cumulative</th><th>Demand</th><th>Solar</th><th>Net Grid</th><th>SOC</th><th>Battery</th><th>Overvolt</th><th>Unmet</th></tr>
        {worst_rows}
      </table>
    </section>

    <section class="card">
      <h2>Artifacts</h2>
      <p><a href="steps.csv">steps.csv</a> &middot; <a href="metrics.json">metrics.json</a> &middot; <a href="meta.json">meta.json</a></p>
    </section>
  </div>
</body>
</html>
"""
    path.write_text(body, encoding="utf-8")
    return path


def _open_report(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
        print(f"opened report: {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [report] could not open browser: {exc}", file=sys.stderr)


def _leaderboard_points(score: float, baselines: dict | None) -> float | None:
    """Points for a raw cost: 0 at the naive baseline, 100 at optimal, capped 150.
    None if the scenario ships no naive/optimal baseline."""
    if not baselines:
        return None
    naive = baselines.get("naive_cost")
    optimal = baselines.get("optimal_cost")
    if naive is None or optimal is None or naive == optimal:
        return None
    return max(0.0, min(150.0, 100.0 * (naive - score) / (naive - optimal)))


def _print_summary(
    summary: dict, breakdown: dict, wall_seconds: float, out_dir: Path,
    report_path: Path, scenario_id: str = "", baselines: dict | None = None,
) -> None:
    print()
    print(f"final_score          ${summary['final_score']:>12.2f}   (lower wins)")
    _pts = _leaderboard_points(summary["final_score"], baselines)
    if _pts is not None:
        print(f"points (leaderboard) {_pts:>12.1f}   (0=naive, 100=optimal, capped 150)")
    print(f"renewable_ratio       {summary['renewable_ratio']:>12.3f}")
    print(f"unmet_demand_total    {summary['unmet_demand_total']:>12.3f} MWh")
    if summary.get("controller_errors"):
        print(f"controller_errors     {summary['controller_errors']:>12d}")
    print(f"wall_clock            {wall_seconds:>12.2f} s")

    rungs = _anchor_rungs(scenario_id, summary["final_score"])
    if rungs:
        print()
        print("how you're doing (raw cost on this scenario, lower wins):")
        for label, cost, note in rungs:
            mark = "->" if label == "you" else "  "
            tail = f"   {note}" if note else ""
            print(f"  {mark} {label:<15s} ${cost:>12,.0f}{tail}")

    lever = _biggest_lever(breakdown, summary["final_score"])
    if lever:
        comp, val, share, hint = lever
        print()
        print(f"biggest lever: {comp} ${val:,.0f} ({share:.0f}% of your cost)")
        print(f"  {hint}")

    if breakdown:
        print()
        print("cost breakdown:")
        for k, v in sorted(breakdown.items(), key=lambda kv: -abs(kv[1])):
            if k == "total":
                continue  # duplicates final_score
            print(f"  {k:<28s} ${v:>10.2f}")
    print()
    print(f"artifacts: {out_dir}")
    print(f"report:    {report_path}")


def _disambiguate_names(paths: list[Path]) -> list[str]:
    """Pick a short unique name for each controller path. Falls back to
    ``<parent>__<stem>`` for stem collisions (e.g. two ``ctrl.py`` files
    in different folders)."""
    stems = [p.stem for p in paths]
    if len(set(stems)) == len(stems):
        return stems
    return [f"{p.parent.name}__{p.stem}" for p in paths]


def run_sweep(
    controller_paths: list[Path],
    scenario_id: str,
    out_dir: Path | None = None,
    plots: bool = True,
    max_steps: int | None = None,
    verbose: bool = True,
) -> dict:
    """Run several controllers against the same scenario and produce a
    side-by-side comparison.

    Writes ``runs/sweep_<scenario>_<ts>/`` containing:
      * one ``<controller_name>/`` per controller (same artifacts
        ``run_playtest`` writes for solo runs)
      * ``comparison.csv`` — long-format rows for pandas pivots
      * ``comparison.png`` — overlay of SOC, cumulative cost, net grid
      * ``per_controller.png`` — small multiples for forensic inspection
      * ``summary.json`` — ranked controller scores + breakdowns
    """
    if not controller_paths:
        raise ValueError("controller_paths must be non-empty")

    controller_paths = [Path(p).resolve() for p in controller_paths]
    names = _disambiguate_names(controller_paths)

    if out_dir is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = Path("runs") / f"sweep_{scenario_id}_{ts}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sweep: list[dict] = []
    for name, path in zip(names, controller_paths):
        if verbose:
            print(f"[{name}] running... ", end="", flush=True)
        sub_out = out_dir / name
        try:
            res = run_playtest(
                controller_path=path,
                scenario_id=scenario_id,
                out_dir=sub_out,
                plots=plots,
                max_steps=max_steps,
                verbose=False,
            )
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"FAILED: {exc}")
            sweep.append({"name": name, "path": str(path), "error": str(exc)})
            continue
        score = res["metrics"]["final_score"]
        if verbose:
            print(f"score=${score:.2f}")
        sweep.append(
            {
                "name": name,
                "path": str(path),
                "metrics": res["metrics"],
                "breakdown": res["breakdown"],
                "rows": res["rows"],
                "out_dir": res["out_dir"],
            }
        )

    successful = [s for s in sweep if "rows" in s]
    if successful:
        _write_comparison_csv(out_dir / "comparison.csv", successful)
        _write_sweep_summary_json(out_dir / "summary.json", sweep)
        if plots:
            _maybe_write_sweep_plots(out_dir, successful)

    if verbose:
        _print_ranked_table(sweep, scenario_id, out_dir)

    return {"sweep": sweep, "out_dir": str(out_dir)}


def _write_comparison_csv(path: Path, sweep: list[dict]) -> None:
    if not sweep:
        return
    fieldnames = ["controller"] + list(sweep[0]["rows"][0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in sweep:
            for row in entry["rows"]:
                writer.writerow({"controller": entry["name"], **row})


def _write_sweep_summary_json(path: Path, sweep: list[dict]) -> None:
    payload = []
    for s in sweep:
        if "error" in s:
            payload.append({"controller": s["name"], "error": s["error"]})
            continue
        payload.append(
            {
                "controller": s["name"],
                "final_score": s["metrics"]["final_score"],
                "renewable_ratio": s["metrics"]["renewable_ratio"],
                "unmet_demand_total": s["metrics"]["unmet_demand_total"],
                "controller_errors": s["metrics"].get("controller_errors", 0),
                "cost_breakdown": {
                    k: round(v, 4) for k, v in s["breakdown"].items() if k != "total"
                },
            }
        )
    payload.sort(key=lambda d: d.get("final_score", float("inf")))
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _maybe_write_sweep_plots(out_dir: Path, sweep: list[dict]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: E402
    except ImportError:
        print(
            "  [plots] matplotlib not installed; skipping sweep PNGs.",
            file=sys.stderr,
        )
        return
    if not sweep:
        return

    cmap = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(sweep))]

    # 1. comparison.png — three stacked overlays
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    for entry, color in zip(sweep, colors):
        rows = entry["rows"]
        t = [r["time_hours"] for r in rows]
        label = f"{entry['name']}  (${entry['metrics']['final_score']:.0f})"
        axes[0].plot(t, [r["soc"] for r in rows], label=label, color=color)
        axes[1].plot(t, [r["cum_cost"] for r in rows], label=label, color=color)
        axes[2].plot(t, [r["net_grid_power_mw"] for r in rows], label=label, color=color)
    axes[0].set_ylabel("SOC")
    axes[0].set_ylim(0, 1)
    axes[1].set_ylabel("cumulative cost ($)")
    axes[2].set_ylabel("net grid MW (+imp / -exp)")
    axes[2].set_xlabel("hours")
    for ax in axes:
        ax.grid(True, alpha=0.3)
    axes[0].legend(loc="upper right", fontsize=8, ncol=min(2, len(sweep)))
    fig.suptitle("Sweep comparison")
    fig.tight_layout()
    fig.savefig(out_dir / "comparison.png", dpi=120)
    plt.close(fig)

    # 2. per_controller.png — small multiples, one row per controller
    n = len(sweep)
    fig, axes = plt.subplots(n, 2, figsize=(12, 2.6 * n), sharex=True, squeeze=False)
    for i, (entry, color) in enumerate(zip(sweep, colors)):
        rows = entry["rows"]
        t = [r["time_hours"] for r in rows]
        score = entry["metrics"]["final_score"]

        ax = axes[i, 0]
        ax.plot(t, [r["soc"] for r in rows], color=color)
        ax.set_ylim(0, 1)
        ax.set_ylabel("SOC")
        ax.grid(True, alpha=0.3)
        ax.set_title(f"{entry['name']}  -  ${score:.2f}", loc="left", fontsize=10)

        ax = axes[i, 1]
        ax.plot(t, [r["battery_flow_mw"] for r in rows], label="battery", color="tab:blue")
        ax.plot(t, [r["emergency_generator_mw"] for r in rows], label="diesel", color="tab:red")
        ax.plot(t, [r["curtail_solar_mw"] for r in rows], label="curtail", color="tab:green", linestyle="--")
        ax.set_ylabel("MW")
        ax.grid(True, alpha=0.3)
        if i == 0:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1, 0].set_xlabel("hours")
    axes[-1, 1].set_xlabel("hours")
    fig.suptitle("Per-controller forensics")
    fig.tight_layout()
    fig.savefig(out_dir / "per_controller.png", dpi=120)
    plt.close(fig)


def _print_ranked_table(sweep: list[dict], scenario_id: str, out_dir: Path) -> None:
    print()
    print(f"Sweep results: {scenario_id}")
    print("-" * 78)
    print(f"  {'rank':>4}  {'controller':<28s}  {'final_score':>12s}  top cost components")
    print("-" * 78)

    ok = [s for s in sweep if "metrics" in s]
    bad = [s for s in sweep if "error" in s]
    ok.sort(key=lambda s: s["metrics"]["final_score"])

    for rank, s in enumerate(ok, 1):
        score = s["metrics"]["final_score"]
        bd = {k: v for k, v in s["breakdown"].items() if k != "total"}
        top = sorted(bd.items(), key=lambda kv: -abs(kv[1]))[:3]
        top_str = ", ".join(f"{k}=${v:.0f}" for k, v in top)
        print(f"  {rank:>4}  {s['name']:<28s}  ${score:>10.2f}    {top_str}")
    for s in bad:
        print(f"  {'!':>4}  {s['name']:<28s}  {'ERROR':>12s}    {s['error']}")
    print("-" * 78)
    print(f"artifacts: {out_dir}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m watt_the_hack.playtest",
        description="Run one or more controllers against a Watt The Hack scenario locally.",
    )
    p.add_argument(
        "controller",
        nargs="*",
        type=Path,
        help="Path(s) to .py files containing a `Strategy` class or `controller` function. "
             "Pass multiple paths (or a shell glob like `probes/*.py`) for a side-by-side sweep.",
    )
    p.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Scenario id (e.g. 'duck_curve'). See --list-scenarios.",
    )
    p.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List available scenarios and exit.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory. Default: runs/<scenario>_<timestamp>/",
    )
    p.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip PNG generation (matplotlib not required).",
    )
    p.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Truncate the run after N steps (for fast iteration).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-run console summary; just write artifacts.",
    )
    p.add_argument(
        "--open-report",
        action="store_true",
        help="Open the generated report.html in your default browser after the run.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_scenarios:
        rows = list_scenarios(include_judging=False)
        if not rows:
            print("No scenarios found.")
            return 0
        width = max(len(s["id"]) for s in rows)
        for s in rows:
            print(f"  {s['id']:<{width}s}  {s['pool']:<10s} {s['title']}")
        return 0

    if not args.controller or args.scenario is None:
        print(
            "Usage: python -m watt_the_hack.playtest <controller.py> [more.py ...] --scenario <id>\n"
            "       python -m watt_the_hack.playtest --list-scenarios",
            file=sys.stderr,
        )
        return 2

    if len(args.controller) == 1:
        run_playtest(
            controller_path=args.controller[0],
            scenario_id=args.scenario,
            out_dir=args.out,
            plots=not args.no_plots,
            max_steps=args.steps,
            verbose=not args.quiet,
            open_report=args.open_report,
        )
    else:
        if args.open_report:
            print(
                "  [report] --open-report is currently only supported for single-controller runs.",
                file=sys.stderr,
            )
        run_sweep(
            controller_paths=args.controller,
            scenario_id=args.scenario,
            out_dir=args.out,
            plots=not args.no_plots,
            max_steps=args.steps,
            verbose=not args.quiet,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
