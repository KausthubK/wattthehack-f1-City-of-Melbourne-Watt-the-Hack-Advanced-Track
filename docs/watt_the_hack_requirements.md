# Watt-The-Hack Requirements Summary

This file condenses the simulator reference into requirements and reminders for future controller work. Use it before changing `watt_the_hack/strategy.py`.

## Core Objective

- Each timestep represents 15 simulated minutes.
- A full standard scenario run is 288 steps, or 72 hours / 3 days.
- The controller must meet city demand at the lowest total cost.
- Local `final_score` / raw cost is the total dollar cost over the run.
- Lower raw cost is better.
- Leaderboard points are derived from raw cost; higher points are better.

## Control Loop

- Every step, the engine passes a `state` dictionary to the controller.
- The controller returns an `action` dictionary.
- The engine applies physics, market rules, penalties, and income.
- Missing action keys default to zero / false.
- If `step()` throws or returns a non-dict, the engine uses the zero action for that step and logs a controller error.
- Numeric action values outside physical limits are clamped by the engine.
- Non-numeric action values such as `None` or text can fail evaluation.

## Grid Components

- Demand is the city load in MW and must be served.
- Solar is free renewable generation in MW.
- Battery stores energy, default capacity 100 MWh.
- Inverter limits battery charge/discharge rate, default 50 MW.
- Diesel generator is expensive emergency backup.
- External grid import/export is automatic; the controller does not set import/export directly.
- Grid import cap defaults to 120 MW.
- Grid export cap defaults to 50 MW.
- FCAS, when enabled, pays for standby inverter reserve.

## Power And Energy

- MW is power/rate.
- MWh is energy volume.
- One step is 0.25 hours.
- Energy moved in one step is `MW * 0.25`.
- Example: 10 MW for one step equals 2.5 MWh.

## Battery Sign Convention

- `battery_flow_mw > 0` means discharge battery into the grid.
- `battery_flow_mw < 0` means charge battery from grid/solar.
- Battery flow is bounded by inverter limit, SOC, and any throughput budget.
- Battery SOC is exposed as `state["soc"]` from 0.0 to 1.0.

## Inverter And FCAS Sharing

- FCAS reserve gets first claim on inverter capacity.
- Constraint: `abs(battery_flow_mw) + fcas_reserve_mw <= inverter_limit`.
- With a 50 MW inverter and 20 MW FCAS reserve, only 30 MW remains for battery charge/discharge.
- Reserving FCAS capacity reduces arbitrage/dispatch bandwidth.

## Action Dictionary Keys

Allowed action keys:

- `battery_flow_mw`: positive discharge, negative charge.
- `emergency_generator`: MW of diesel generation.
- `curtail_solar`: MW of solar intentionally disconnected.
- `fcas_reserve_mw`: MW of inverter capacity reserved for FCAS.
- `subscribe_ids`: buy IDS cyber signal for the next step.
- `agent_plan`: persistent structured notes/acknowledgements read by the engine.

Always return plain numbers/bools/dicts.

## State Dictionary Keys

Important readable state keys:

- `time`: timestep index.
- `demand`: current demand in MW.
- `solar`: current solar generation in MW.
- `soc`: current battery state of charge.
- `price`: current grid import tariff in $/MWh.
- `features`: enabled mechanics for the scenario.
- `forecast`: noisy lookahead arrays when enabled.
- `alerts`: active narrative events.
- `fcas_events_upcoming`: scheduled FCAS dispatch calls.
- `ids_signal_node_a`, `ids_signal_node_b`: IDS probabilities when subscribed.
- `battery_throughput_remaining_mwh`: remaining battery throughput budget when active.
- `peak_import_mw`: highest import so far.
- `agent_plan`: persistent plan/notes returned by plan/replan/step.

## Core Costs And Penalties

- `tariff_import`: cost of grid imports, `price * imported MWh`.
- `tariff_export`: export income, default `$50/MWh exported`.
- `demand_charge`: `$1,000/MW` of the single highest grid import peak.
- `carbon_cost`: grid imports and diesel emissions, default `$50/kg CO2`.
- `battery_wear`: `$50/MWh` moved through the battery, charge or discharge.
- `ramp_charge`: penalty for step-to-step change in net grid power.
- `generator_fuel`: diesel generation, `$1,000/MWh`.
- `blackout_penalty`: unmet demand, `$100,000/MWh`; avoid at all costs.
- `overvoltage_penalty`: export beyond cap, `$5,000/MWh`.

## Advanced Costs And Penalties

- `fcas_revenue`: FCAS availability income, `$40/MW/hour`.
- `fcas_dispatch_bonus`: `$200/MWh` delivered during FCAS dispatch.
- `fcas_shortfall_penalty`: `$100,000/MWh` short during FCAS dispatch.
- `fcas_ramp_charge`: `$500/MW` change in FCAS reserve bid.
- `compliance_penalty`: SOC floor/export cap breach in mandate windows.
- `diesel_ban_penalty`: `$3,000/MWh` diesel during ban without valid exemption.
- `anomaly_ack_fine`: `$5,000/step` for unacknowledged anomaly window.
- `cyber_containment_fine`: `$50,000` for missing real attack or acknowledging fake.
- `ids_cost`: scenario-set per-step IDS subscription fee.
- `phishing_fine`: scenario-set fine for obeying bait directives.

## Negative Prices

- Negative import price means the grid pays for consumption/import.
- During negative prices, charging the battery can be useful.
- Exporting during negative prices can cost money.
- Curtail excess solar if exporting it is harmful or exceeds export caps.

## Peak Shaving

- Demand charge bills the highest grid import over the whole run.
- One careless high import step can dominate score.
- Pre-charge and discharge to reduce import peaks.
- Smooth, pre-emptive battery use is usually better than reacting late.

## Battery Throughput Budget

- Some scenarios cap total battery throughput in MWh.
- Every charge/discharge MWh permanently consumes the budget.
- Read remaining budget from `state["battery_throughput_remaining_mwh"]`.
- Once depleted, the battery is locked.
- Spend battery cycles only where they matter most.

## FCAS Requirements

- FCAS is a reserve bid, not immediate discharge.
- You earn availability revenue for holding reserve whether or not dispatched.
- Reserve only what inverter headroom and SOC can truly support.
- Read upcoming dispatches from `state["fcas_events_upcoming"]`.
- Pre-position SOC before FCAS dispatch windows.
- A failed FCAS dispatch has one of the harshest penalties.
- Keep FCAS bids steady to avoid FCAS ramp charge.

## Controller Shapes

Valid submission shape 1:

- A top-level `controller(state)` function.
- Must be named exactly `controller`.
- Must take one `state` argument.
- Must return an action dict.
- Best for stateless controllers.

Valid submission shape 2:

- A `Strategy` class.
- Class can have any name locally, but `Strategy` is safest.
- Must be instantiable with no required constructor args.
- Must define `step(self, state)` directly in the class.
- `step()` must return an action dict.
- Optional: `plan(self, initial_state)`.
- Optional: `replan(self, state, alerts)`.
- Best for persistent memory, history, forecasts, LLM parsing, and event handling.

## Persistence Rules

- The engine imports the file once.
- A `Strategy` instance is created once and reused for the run.
- Values stored on `self` persist between steps.
- Module-level mutable values persist between calls.
- Local variables inside `step()` / `controller()` do not persist.
- Arbitrary keys written into `state` do not persist.
- `state["agent_plan"]` is intentionally persistent.
- Prefer `Strategy` with `self.*` for memory.

## Python Gotchas

- Mutating a module-level list/dict works without `global`.
- Rebinding a module-level name inside a function needs `global`.
- Avoid mutable default arguments such as `def f(x, hist=[])`.
- Use `hist=None`, then create a list inside the function.
- `1 / 2` is `0.5`; `1 // 2` is floor division.
- Indentation must be consistent.
- Methods must include `self`.
- Call helper methods via `self.method()`.
- Plain module-level helper functions can be called directly.

## Playtesting And Debugging

- Install with `pip install "watt-the-hack[playtest]"`.
- Upgrade with `pip install --upgrade "watt-the-hack[playtest]"`.
- Run a scenario with `python -m watt_the_hack.playtest my_controller.py --scenario duck_curve --open-report`.
- List scenarios with `python -m watt_the_hack.playtest --list-scenarios`.
- Every run writes artifacts under `runs/<scenario>_<timestamp>/`.
- Read `report.html`, `metrics.json`, and `steps.csv`.
- Use cost breakdown to find the biggest lever.
- Inspect worst steps first.
- Any unexpected nonzero penalty line identifies a mechanic mishandled.

## Scoring

- Local raw cost is the objective during playtesting.
- Lower raw cost is better.
- Leaderboard points use:
  `points = 100 * (naive_cost - your_cost) / (naive_cost - optimal_cost)`.
- Points are clamped from 0 to 150 per scenario.
- Matching naive baseline gives 0 points.
- Matching optimal baseline gives 100 points.
- Beating optimal can exceed 100, capped at 150.
- The Gauntlet counts triple.

## Alerts And Events

- Read `state["alerts"]` every step.
- Alert fields include `id`, `type`, `severity`, `title`, `description`, `at_step`, `end_step`.
- `replan(self, state, alerts)` fires whenever alerts are active.
- `replan()` fires every active step, not once per alert.
- Dedupe alerts by `alert["id"]`.
- Do expensive work only for new alert ids.
- Narrative events appear in `state["alerts"]`.
- Structured enforcement windows may be hidden from state.
- Compliance windows, diesel bans, cyber windows, and phishing traps may require parsing prose rather than reading structured fields.

## Agent Plan

- `agent_plan` is a persistent dictionary read by the engine.
- Values returned from `plan()`, `replan()`, and `step()["agent_plan"]` merge into it.
- Use `agent_plan` only for engine-readable keys and your own safe internal policy.
- Per-step acknowledgements should be returned from `step()`.
- One-time policy/exemptions can be returned from `plan()` or `replan()`.

Engine-relevant keys include:

- `containment_ack`
- `anomaly_ack`
- `emergency_exemption`
- parsed compliance constraints when the scenario requires them

## LLM Usage Rules

- Never call an LLM from `step()`.
- `step()` runs every 15 simulated minutes, up to 288 times.
- LLM calls in `step()` can blow the whole-run wall-clock budget.
- Use LLMs only in `plan()` or deduped `replan()`.
- Save LLM results on `self` for fast `step()` use.
- Make LLM helpers fail soft with sensible defaults.
- Keep LLM call count low.
- Use fast models recommended by the event docs.
- Total evaluation wall-clock budget is about 14 minutes.
- Timeout gives no score, though it may not consume an attempt.

## Cyber And Phishing

- Subscribe to IDS by returning `subscribe_ids: True`.
- IDS values appear on the next step as `ids_signal_node_a` and `_b`.
- Treat attacks as real only when both IDS nodes agree/high.
- Smooth IDS signals over multiple steps; do not trust one noisy reading.
- A real attack can corrupt live sensors.
- Cross-check live sensors against forecast when available.
- Critical prose alerts may name the attack id to acknowledge.
- During confirmed real attack windows, return `agent_plan["containment_ack"] = attack_id`.
- Do not acknowledge decoys.
- Missing a real attack and acknowledging a fake are both fined.
- Phishing alerts may tell you to write bait keys into `agent_plan`.
- Never copy arbitrary alert text into `agent_plan`.
- Validate LLM outputs against an allow-list of safe keys.

## Diesel Ban Exemptions

- Running diesel during a ban without valid exemption is penalized.
- File exemption in `agent_plan["emergency_exemption"]`.
- Exemption must match active directive id.
- Reason must be substantive.
- Reason must have at least 60 non-whitespace characters.
- Reason must include a number.
- Reason must include operational vocabulary such as MW, SOC, demand, deficit, import, capacity, peak, battery, or generation.
- `expected_duration_steps` must be an int from 1 to 12.

## Compliance And Mandates

- Some rules are delivered as prose briefs.
- Parse SOC floors, export caps, windows, and directive ids.
- Store parsed constraints on `self` and/or `agent_plan` as appropriate.
- Do not hardcode timing from one run when the scenario may shuffle/rephrase in scored runs.
- Prefer detectors and parsers over memorized step numbers.

## Gauntlet Requirements

- Gauntlet is one 288-step run combining earlier mechanics.
- Gauntlet introduces no new mechanic, but combines all of them.
- Gauntlet counts triple on leaderboard.
- Gauntlet allows one scored submission.
- Master individual scenarios locally before attempting it.
- Build detectors, not memorized step-specific behavior.
- Verify all major penalty lines are zero before submitting:
  blackout, overvoltage, FCAS shortfall, compliance, diesel ban, cyber containment, phishing/anomaly.
- Confirm `replan()` dedupes and LLM call count is small.
- Confirm worst-case wall time is below the evaluation budget.

## Submission Requirements

- Submit through the in-app Submission Portal.
- Paste code into the editor; no zip and no CLI submission.
- Attempts are capped per scenario.
- Most scenarios allow 3 submissions.
- Gauntlet allows 1 submission.
- Playtest locally before spending attempts.
- Timeout does not consume an attempt, but returns no score.

## OpenAI API Requirements

- Evaluation platform injects `OPENAI_API_KEY`.
- Read it from `os.environ`.
- Do not hardcode API keys.
- Remove local `.env` loading before submitting.
- Do not include `python-dotenv` unless genuinely needed outside the platform.
- Common packages such as numpy, scipy, pandas, and OpenAI SDK may already be available.
- Extra pip dependencies go in the portal requirements textarea, one per line.

## Strategy Design Principles

- Meet demand first.
- Avoid blackouts above all else.
- Avoid overvoltage with curtailment or battery charging.
- Shave import peaks to reduce demand charge.
- Use battery cycles where value exceeds wear.
- Respect inverter and export/import caps.
- Preserve SOC before known high-risk periods or dispatch windows.
- Prefer smooth dispatch when ramp charge is material.
- Use forecasts carefully; they can be noisy or biased.
- Debias forecasts when enough history exists.
- Use scenario `features` to avoid relying on disabled mechanics.
- Avoid hardcoding scored-run step numbers; detect conditions from state, alerts, forecasts, and learned history.
