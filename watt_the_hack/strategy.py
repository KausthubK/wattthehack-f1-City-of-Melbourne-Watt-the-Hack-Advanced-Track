def controller(state):
    demand = float(state["demand"])
    solar = float(state["solar"])
    soc = float(state["soc"])
    price = float(state.get("price", 0.0))
    step = int(state.get("time", 0))
    day_step = step % 96

    surplus = solar - demand
    flow = 0.0

    # Charge aggressively from midday solar surplus.
    if surplus > 0 and soc < 0.98:
        flow = -min(50.0, surplus)

    # Spend battery mainly during the duck-curve neck / evening peak.
    elif soc > 0.08 and (price >= 300 or 64 <= day_step <= 84 or demand >= 105):
        target_import = 105.0
        if price >= 450:
            target_import = 70.0
        elif price >= 300:
            target_import = 85.0
        if demand >= 118:
            target_import = 95.0

        flow = max(0.0, min(50.0, demand - solar - target_import))

    # Reliability fallback: avoid breaching the 120 MW grid import cap.
    elif soc > 0.05 and demand - solar > 120:
        flow = min(50.0, demand - solar - 110.0)

    net = demand - solar - flow

    curtail = max(0.0, -net - 50.0)
    emergency_generator = max(0.0, net - 120.0)

    return {
        "battery_flow_mw": flow,
        "curtail_solar": curtail,
        "emergency_generator": emergency_generator,
    }

# --- Local playtest. Runs on `python strategy.py`; the judge ignores this block. ---
if __name__ == "__main__":
    from watt_the_hack.playtest import run_playtest
    result = run_playtest(__file__, "duck_curve", plots=True, open_report=True)
    print(f"\nRaw cost (lower wins): ${result['metrics']['final_score']:,.2f}")