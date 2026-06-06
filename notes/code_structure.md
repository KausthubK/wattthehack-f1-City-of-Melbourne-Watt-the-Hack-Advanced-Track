# Code Structure

Most of this repo is the game engine and local simulation harness. Controller work mostly lives in `strategy.py`, supported by scenario files, reports, reference controllers, and the engine code as the rules reference.

## Main Controller Surface

- `strategy.py`
  - Main file to edit while developing a controller.
  - For simple scenarios, expose a top-level `controller(state)` function.
  - For agentic scenarios, you can use a `Strategy` class with `plan`, `replan`, and `step`.

- `watt_the_hack/simulation/strategy.py`
  - Adapter that supports multiple controller shapes.
  - Accepts a top-level `controller`, a `Strategy` class, an instance with `.step`, or a bare callable.
  - Useful for understanding what file shapes the local playtest and judge can run.

## Useful For Building Controllers

- `watt_the_hack/controllers/rule_based.py`
  - Readable baseline controller.
  - Good for learning the action keys and simple dispatch rules.

- `watt_the_hack/controllers/parametric.py`
  - Debug/simple controller factory.
  - Emits fixed battery, diesel, curtailment, and FCAS values every step.
  - Useful for experiments, but not a serious strategy by itself.

- `watt_the_hack/scenarios/synthetic/duck_curve.json`
  - Local rule-based practice scenario.
  - Contains demand, solar, price, feature flags, events, and scoring baselines.
  - Useful for understanding the public practice case.

- `watt_the_hack/scenarios/synthetic/agentic_demo.json`
  - Local agentic practice scenario.
  - Adds alerts, forecast, FCAS, and a compliance window.
  - Useful for learning the `plan` / `replan` / `step` pattern.

- `watt_the_hack/engine/engine.py`
  - Best rules-of-the-game reference.
  - Defines physical limits, clipping behavior, SOC math, feature gating, forecasts, alerts, and every cost component.
  - Read it as a spec more than something to modify.

- `watt_the_hack/playtest.py`
  - Local evaluation tool.
  - Runs a controller against a scenario.
  - Writes reports, plots, cost breakdowns, worst steps, diagnostics, and hints.

- `runs/*`
  - Local playtest outputs.
  - `report.html` is useful for visual inspection.
  - `steps.csv` is useful for debugging individual timesteps and cost spikes.

- `tests/test_engine.py`
  - Useful executable documentation for edge cases.
  - Covers compliance penalties, forecast behavior, diesel bans, controller visibility, and clipping.

## Mostly Simulation Infrastructure

- `watt_the_hack/engine/`
  - Physics and market engine.
  - Applies controller actions, clips to limits, updates SOC, and computes cost.

- `watt_the_hack/metrics/`
  - Accumulates run metrics.
  - `final_score` is the accumulated raw dollar cost.

- `watt_the_hack/simulation/boot.py`
  - Loads a scenario.
  - Applies scenario config overrides.
  - Creates initial forecast and alerts.

- `watt_the_hack/simulation/runner.py`
  - Drives the simulation loop.
  - Calls `plan`, `replan`, and `step`.
  - Runs engine steps and updates metrics.

- `watt_the_hack/data_loaders/`
  - Loads scenario JSON.
  - Hides private future profiles and internal event details from controllers.
  - Useful for understanding visibility, but usually not something to edit.

- `watt_the_hack/api/`
  - FastAPI playground/server layer.
  - Mostly infrastructure unless building a UI or hosted simulator.

- `watt_the_hack/api/sandbox.py`
  - Restricts user-provided controller source in the web/API path.
  - Not relevant for normal strategy tuning.

## Supporting Files

- `notebooks/`
  - Training and playtest notebooks.

- `examples/`
  - Small programmatic playtest examples.

- `docs/`
  - Documentation assets, including the control-loop diagram.

- `notes/`
  - Local working notes.

- `pyproject.toml`
  - Package metadata and dependencies.

- `.venv`, `.git`, `.pytest_cache`, `__pycache__`
  - Ignore for controller strategy work.

## Practical Workflow

1. Edit `strategy.py`.
2. Run `python strategy.py`.
3. Inspect the latest `runs/*/report.html` and `runs/*/steps.csv`.
4. Use `engine.py`, the scenario JSON, and `rule_based.py` to reason about the next improvement.
5. Repeat.

