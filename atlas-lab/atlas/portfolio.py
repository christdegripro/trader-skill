"""Paper portfolio simulator. SIMULATED money, no broker, no order endpoint.

On each debate date t (decided after the close) the CIO's LONG calls become
target weights. Orders fill at the OPEN of t + execution_delay_days, at a
worse price by slippage_bps, and pay fee_bps (at least min_fee_usd). Long
only, each name capped at max_position_pct, a cash buffer kept. The account
is marked at every close; drawdown and turnover are tracked. The benchmark
is bought once at the first fill and held.
"""

import math


def target_weights(cio_out, cfg):
    min_c = cfg.get("min_conviction", 55)
    cap = cfg["max_position_pct"] / 100
    invest = 1 - cfg["cash_buffer_pct"] / 100
    longs = [r for r in cio_out["recommendations"] if r["direction"] == "LONG" and r["conviction"] >= min_c]
    total = sum(r["conviction"] for r in longs)
    w = {}
    for r in longs:
        w[r["ticker"]] = min(cap, invest * r["conviction"] / total)
    return w


def _fee(notional, cfg):
    return max(cfg.get("min_fee_usd", 0.0), abs(notional) * cfg.get("fee_bps", 0.0) / 10000)


def simulate(bars, debates, cfg):
    sim = cfg["simulation"]
    rules = dict(sim, max_position_pct=cfg["max_position_pct"], cash_buffer_pct=cfg["cash_buffer_pct"])
    slip = sim.get("slippage_bps", 0.0) / 10000
    delay = sim.get("execution_delay_days", 1)
    bench = cfg["benchmark"]
    debates = sorted(debates, key=lambda d: d["as_of"])
    result = {"label": "SIMULE", "equity": [], "trades": [], "metrics": {}, "warnings": []}
    if not debates:
        result["warnings"].append("aucun debat: rien a simuler")
        return result

    orders_on = {}  # fill date -> target weights
    for d in debates:
        fill = bars.shift(d["as_of"], delay)
        if fill is None:
            result["warnings"].append("debat du %s: pas encore de seance d'execution" % d["as_of"])
            continue
        orders_on[fill] = target_weights(d["agents"]["cio"], rules)

    cash = float(sim["starting_cash"])
    pos = {}
    fees = turnover = 0.0
    bench_units = None
    bench_cash = float(sim["starting_cash"])
    peak, max_dd = None, 0.0
    first = min(orders_on) if orders_on else None
    if first is None:
        return result
    # stop 20 sessions after the last decision: no drifting into periods nobody looked at
    stop = bars.shift(debates[-1]["as_of"], sim.get("hold_after_last_debate_days", 20)) or bars.last_date()
    for day in [d for d in bars.dates() if first <= d <= stop]:
        if day in orders_on:
            targets = orders_on[day]
            # value at the open to size the trades
            value = cash + sum(q * (bars.open(s, day) or 0) for s, q in pos.items())
            trades = []
            for s in sorted(set(pos) | set(targets)):
                px = bars.open(s, day)
                if px is None:
                    result["warnings"].append("%s: pas de prix d'ouverture le %s, ordre ignore" % (s, day))
                    continue
                want = targets.get(s, 0.0) * value / px
                delta = want - pos.get(s, 0.0)
                if abs(delta * px) < sim.get("min_trade_usd", 1.0):
                    continue
                trades.append((s, delta, px))
            for s, delta, px in sorted(trades, key=lambda t: t[1]):  # sells first
                fill_px = px * (1 + slip) if delta > 0 else px * (1 - slip)
                notional = delta * fill_px
                fee = _fee(notional, sim)
                if delta > 0 and notional + fee > cash:
                    delta = max(0.0, (cash - fee) / fill_px)
                    notional = delta * fill_px
                    if delta <= 0:
                        continue
                cash -= notional + fee
                fees += fee
                turnover += abs(notional)
                pos[s] = pos.get(s, 0.0) + delta
                if abs(pos[s]) < 1e-9:
                    del pos[s]
                result["trades"].append({"date": day, "symbol": s, "side": "achat" if delta > 0 else "vente",
                                         "qty": round(delta, 6), "price": round(fill_px, 4),
                                         "fee": round(fee, 2), "simulated": True})
            if bench_units is None and bars.open(bench, day):
                bpx = bars.open(bench, day) * (1 + slip)
                bfee = _fee(bench_cash, sim)
                bench_units = (bench_cash - bfee) / bpx
                bench_cash = 0.0
        equity = cash + sum(q * (bars.close(s, day) or 0) for s, q in pos.items())
        bclose = bars.close(bench, day)
        bequity = bench_cash + (bench_units or 0) * (bclose or 0)
        peak = equity if peak is None else max(peak, equity)
        dd = equity / peak - 1
        max_dd = min(max_dd, dd)
        result["equity"].append({"date": day, "equity": round(equity, 2), "benchmark": round(bequity, 2),
                                 "drawdown": round(dd, 4), "cash": round(cash, 2)})

    eq = [e["equity"] for e in result["equity"]]
    start = float(sim["starting_cash"])
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    m = result["metrics"]
    m["days"] = len(eq)
    m["start_value"] = start
    m["end_value"] = eq[-1] if eq else start
    m["return_pct"] = (m["end_value"] / start - 1) * 100
    m["benchmark_return_pct"] = (result["equity"][-1]["benchmark"] / start - 1) * 100 if eq else 0.0
    m["max_drawdown_pct"] = max_dd * 100
    m["fees_usd"] = round(fees, 2)
    m["turnover_usd"] = round(turnover, 2)
    m["trades"] = len(result["trades"])
    min_days = sim.get("min_days_for_sharpe", 60)
    if len(rets) >= min_days:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        m["sharpe"] = mu / sd * math.sqrt(252) if sd > 0 else None
    else:
        m["sharpe"] = None
        result["warnings"].append("Sharpe non affiche: %d jours < %d" % (len(rets), min_days))
    return result
