"""Scorecard: every recommendation against what happened next.

A prediction made at the close of day t with horizon h is scored on the
close-to-close return from t to t+h trading days, minus the benchmark over
the same days (so a LONG only scores if the stock beat the market).
Score = sign(direction) x excess return x conviction/100. NEUTRAL is not
scored. A prediction whose t+h is not in the data yet stays "en attente".
"""

import math

SIGN = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}


def split_of(date, cfg):
    s = cfg["splits"]
    if date <= s["train_end"]:
        return "train"
    if date <= s["validation_end"]:
        return "validation"
    if date <= s["test_end"]:
        return "test"
    return "live"


def forward_return(bars, sym, as_of, h):
    end = bars.shift(as_of, h)
    if end is None:
        return None
    a, b = bars.close(sym, as_of), bars.close(sym, end)
    if not a or b is None:
        return None
    return b / a - 1


def score_rec(bars, rec, as_of, benchmark):
    h = rec["horizon_days"]
    r = forward_return(bars, rec["ticker"], as_of, h)
    rb = forward_return(bars, benchmark, as_of, h)
    if r is None or rb is None:
        return {"status": "en attente", "return": None, "excess": None, "score": None}
    excess = r - rb
    sign = SIGN[rec["direction"]]
    if sign == 0:
        return {"status": "neutre", "return": r, "excess": excess, "score": None}
    score = sign * excess * rec["conviction"] / 100
    return {"status": "note", "return": r, "excess": excess, "score": score, "hit": sign * excess > 0}


def predictions(bars, debates, cfg):
    rows = []
    for d in debates:
        for agent, out in d["agents"].items():
            for rec in out["recommendations"]:
                res = score_rec(bars, rec, d["as_of"], cfg["benchmark"])
                rows.append({"as_of": d["as_of"], "split": split_of(d["as_of"], cfg), "agent": agent,
                             "layer": out["layer"], "ticker": rec["ticker"], "direction": rec["direction"],
                             "conviction": rec["conviction"], "horizon_days": rec["horizon_days"],
                             "claim": rec["claim"], **res})
    return rows


def stats(scores, horizon, min_n):
    """Mean, hit rate, 95% interval; Sharpe only with enough points."""
    n = len(scores)
    out = {"n": n, "mean": None, "ci95": None, "sharpe": None, "note": None}
    if n == 0:
        out["note"] = "aucune prediction notee"
        return out
    m = sum(scores) / n
    out["mean"] = m
    if n >= 2:
        sd = math.sqrt(sum((s - m) ** 2 for s in scores) / (n - 1))
        half = 1.96 * sd / math.sqrt(n)
        out["ci95"] = [m - half, m + half]
        if n >= min_n and sd > 0:
            out["sharpe"] = m / sd * math.sqrt(252 / horizon)
    if n < min_n:
        out["note"] = "echantillon trop petit (%d < %d) pour un Sharpe" % (n, min_n)
    return out


def scorecard(rows, cfg):
    """{agent: {split: {"all": stats, "hit_rate": x, "pending": k}}}"""
    min_n = cfg.get("min_predictions_for_sharpe", 30)
    card = {}
    for r in rows:
        a = card.setdefault(r["agent"], {})
        s = a.setdefault(r["split"], {"scores": [], "hits": 0, "pending": 0, "neutral": 0, "horizons": []})
        if r["status"] == "note":
            s["scores"].append(r["score"])
            s["hits"] += 1 if r["hit"] else 0
            s["horizons"].append(r["horizon_days"])
        elif r["status"] == "en attente":
            s["pending"] += 1
        else:
            s["neutral"] += 1
    out = {}
    for agent, splits in card.items():
        out[agent] = {}
        for split, s in splits.items():
            h = round(sum(s["horizons"]) / len(s["horizons"])) if s["horizons"] else 5
            st = stats(s["scores"], max(1, h), min_n)
            st["hit_rate"] = s["hits"] / len(s["scores"]) if s["scores"] else None
            st["pending"] = s["pending"]
            st["neutral"] = s["neutral"]
            out[agent][split] = st
    return out
