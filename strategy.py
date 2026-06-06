# strategy.py — edit the controller, then run:  python strategy.py
def controller(state):
    # Duck curve 101: bank the midday solar surplus, spend it at the evening peak.
    demand, solar, soc = state["demand"], state["solar"], state["soc"]
    surplus = solar - demand                      # +ve = excess solar right now
    flow = 0.0
    if surplus > 5 and soc < 0.9:                 # midday: store the excess
        flow = -min(20.0, surplus)                # negative = charge
    elif surplus < 0 and soc > 0.2:               # evening: cover the shortfall
        flow = min(20.0, -surplus)                # positive = discharge
    net = demand - solar - flow                   # what's left for the grid
    curtail = max(0.0, -net - 50.0)               # dump unstorable export
    return {"battery_flow_mw": flow, "curtail_solar": curtail}


# --- Local playtest. Runs on `python strategy.py`; the judge ignores this block. ---
if __name__ == "__main__":
    from watt_the_hack.playtest import run_playtest
    result = run_playtest(__file__, "duck_curve", plots=True, open_report=True)
    print(f"\nRaw cost (lower wins): ${result['metrics']['final_score']:,.2f}")
