# Basic Objective And Game Mechanics

The leaderboard objective is to minimise total raw dollar cost. In the code, `final_score == cost_sum`, and lower wins.

The other goals are not separate ranked objectives. They are physical constraints and cost components that feed into the one dollar score.

## Practical Priority Order

1. Avoid blackouts / unmet demand.
   - Grid import is capped at `120 MW`.
   - If demand after solar, battery, and diesel exceeds that cap, the excess becomes `blackout_penalty`.
   - This is very expensive: `$100000/MWh`.

2. Avoid over-export / overvoltage.
   - Grid export is capped at `50 MW`.
   - If solar surplus pushes export beyond that cap, the excess becomes `overvoltage_penalty`.
   - This costs `$5000/MWh`.

3. Keep peak grid import low.
   - The demand charge is based on the highest grid import reached during the run.
   - Each new MW of peak import costs `$1000/MW`.
   - One bad evening spike can hurt the whole run.

4. Avoid unnecessary diesel.
   - Diesel is available as backup, but costs `$1000/MWh` plus carbon.
   - It is mainly useful when it prevents a worse blackout or peak-import cost.

5. Smooth grid power.
   - Ramp charge penalises sudden changes in net grid power.
   - In `duck_curve`, this is softened by the scenario override to `0.01`, but smoother dispatch still helps.

6. Arbitrage energy price.
   - Charge when solar is abundant or prices are low/negative.
   - Discharge when import price is high, especially during evening peaks.

7. Avoid wasteful battery cycling.
   - Battery throughput has wear cost: `$50/MWh`.
   - Cycling is worth it only when it avoids a larger cost or captures useful arbitrage.

## Main Decision Levers

For `duck_curve`, the available control levers are:

- `battery_flow_mw`
  - Positive means discharge the battery.
  - Negative means charge the battery.

- `curtail_solar`
  - Throw away solar generation.
  - Useful when surplus solar would exceed the export cap and cause overvoltage.

- `emergency_generator`
  - Run diesel backup.
  - Expensive, but can avoid blackouts or severe peak import.

- `fcas_reserve_mw`
  - Exists in the engine, but is disabled for `duck_curve`.

## Core Power Balance

```python
net_grid_power = demand - (solar - curtail_solar) - battery_flow_mw - emergency_generator
```

Interpretation:

- Positive `net_grid_power` means importing from the grid.
- Negative `net_grid_power` means exporting to the grid.
- Import above `120 MW` causes unmet demand / blackout penalty.
- Export below `-50 MW` causes overvoltage penalty.

## Duck Curve Strategy Intuition

The useful pattern is:

1. Charge the battery during midday solar surplus.
2. Curtail only the solar that cannot be stored or exported safely.
3. Discharge through the evening ramp to reduce peak imports and expensive grid purchases.
4. Use diesel only when it prevents a much larger penalty.

