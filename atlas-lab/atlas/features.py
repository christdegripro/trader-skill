"""Facts the agents read: computed from a snapshot only, never from later bars."""

import math


def _ret(closes, n):
    if len(closes) <= n or closes[-1 - n] <= 0:
        return None
    return closes[-1] / closes[-1 - n] - 1


def _vol(closes, n=20):
    if len(closes) <= n:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _round(x, k=4):
    return None if x is None else round(x, k)


def symbol_facts(closes):
    facts = {
        "last_close": _round(closes[-1], 2) if closes else None,
        "ret_5d": _round(_ret(closes, 5)),
        "ret_20d": _round(_ret(closes, 20)),
        "ret_60d": _round(_ret(closes, 60)),
        "vol_20d_annual": _round(_vol(closes, 20)),
        "dist_ma50": None,
        "drawdown_from_60d_high": None,
        "history_days": len(closes),
    }
    if len(closes) >= 50:
        ma = sum(closes[-50:]) / 50
        facts["dist_ma50"] = _round(closes[-1] / ma - 1)
    if len(closes) >= 60:
        hi = max(closes[-60:])
        facts["drawdown_from_60d_high"] = _round(closes[-1] / hi - 1)
    return facts


def build(snapshot, tickers, benchmark):
    """Facts per ticker plus the benchmark, with a source tag for citations."""
    as_of = snapshot.last_date()
    out = {"as_of": as_of, "data_source": snapshot.meta.get("source", "inconnue"),
           "benchmark": benchmark, "symbols": {}}
    for sym in list(tickers) + [benchmark]:
        closes = [c for _, c in snapshot.series(sym)]
        f = symbol_facts(closes)
        f["source"] = "bars:%s@%s" % (sym, as_of)
        if closes and snapshot.series(sym)[-1][0] != as_of:
            f["stale"] = True  # no bar on as_of: price is old
        if not closes:
            f["missing"] = True
        out["symbols"][sym] = f
    return out
