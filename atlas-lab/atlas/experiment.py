"""Prompt-change experiments: one edit, one agent, KEEP or REVERT in Git.

1. propose: pick the weakest agent on the TRAIN split, show the backend its
   worst train-period calls, get ONE edit -> agents/<agent>.candidate.md.
2. evaluate: replay that agent with the candidate on VALIDATION debate dates
   only (same snapshot, same upstream views as the original), score both,
   compare paired. KEEP only if the candidate is better with the whole 95%
   interval above zero and enough points; otherwise REVERT.
3. The decision is logged in experiments/log.jsonl and committed to Git.

The TEST split is never read here: evaluate refuses any date in it.
"""

import datetime
import json
import math
import os
import subprocess

from . import debate, features, scoring


class ExperimentError(Exception):
    pass


def _log_path(home):
    return os.path.join(home, "experiments", "log.jsonl")


def read_log(home):
    p = _log_path(home)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _append(home, entry):
    os.makedirs(os.path.join(home, "experiments"), exist_ok=True)
    with open(_log_path(home), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def weakest(card, cfg, exclude=()):
    min_n = cfg["experiments"].get("min_train_predictions", 10)
    best = None
    for agent, splits in card.items():
        st = splits.get("train")
        if agent in exclude or not st or st["n"] < min_n or st["mean"] is None:
            continue
        if best is None or st["mean"] < best[1]:
            best = (agent, st["mean"])
    return best


def propose(home, bars, cfg, backend, agent=None, now=None):
    debates = [d for d in debate.load_all(home) if scoring.split_of(d["as_of"], cfg) == "train"]
    rows = scoring.predictions(bars, debates, cfg)
    card = scoring.scorecard(rows, cfg)
    if agent is None:
        pick = weakest(card, cfg)
        if pick is None:
            raise ExperimentError("pas assez de predictions notees en entrainement pour choisir un agent")
        agent = pick[0]
    cand = debate.prompt_path(home, agent)[:-3] + ".candidate.md"
    if os.path.exists(cand):
        raise ExperimentError("une experience est deja en cours pour %s" % agent)
    mine = [r for r in rows if r["agent"] == agent and r["status"] == "note"]
    worst = sorted(mine, key=lambda r: r["score"])[:12]
    mistakes = [{k: r[k] for k in ("as_of", "ticker", "direction", "conviction", "horizon_days",
                                   "claim", "excess")} for r in worst]
    original = debate.read_prompt(home, agent)
    rev = backend.revise(agent, original, mistakes)
    new_prompt = str(rev.get("new_prompt", "")).strip()
    if not new_prompt or new_prompt == original.strip():
        raise ExperimentError("le backend n'a propose aucune modification")
    with open(cand, "w", encoding="utf-8") as fh:
        fh.write(new_prompt + "\n")
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    exp_id = "%s-%s" % (now[:10], agent)
    entry = {"id": exp_id, "event": "proposed", "agent": agent, "at": now,
             "rationale": rev.get("rationale", ""), "train_mean": card.get(agent, {}).get("train", {}).get("mean"),
             "original_sha": debate.sha(original), "candidate_sha": debate.sha(new_prompt + "\n"),
             "backend": backend.name}
    _append(home, entry)
    return entry


def paired_stats(diffs):
    n = len(diffs)
    if n < 2:
        return {"n": n, "mean_diff": diffs[0] if diffs else None, "ci95": None}
    m = sum(diffs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in diffs) / (n - 1))
    half = 1.96 * sd / math.sqrt(n)
    return {"n": n, "mean_diff": m, "ci95": [m - half, m + half]}


def decide(st, min_n):
    if st["n"] < min_n:
        return "REVERT", "pas assez de points en validation (%d < %d)" % (st["n"], min_n)
    if st["ci95"] is None or st["ci95"][0] <= 0:
        return "REVERT", "gain non credible: l'intervalle a 95 % touche zero ou est negatif"
    return "KEEP", "gain credible sur la validation (intervalle a 95 % au-dessus de zero)"


def evaluate(home, bars, cfg, backend, agent, git=True, now=None):
    cand_path = debate.prompt_path(home, agent)[:-3] + ".candidate.md"
    if not os.path.exists(cand_path):
        raise ExperimentError("aucun candidat pour %s" % agent)
    candidate = debate.read_prompt(home, agent, candidate=True)
    debates = [d for d in debate.load_all(home) if scoring.split_of(d["as_of"], cfg) == "validation"]
    for d in debates:
        if scoring.split_of(d["as_of"], cfg) == "test":  # never, by construction; kept as a tripwire
            raise ExperimentError("date de test interdite: %s" % d["as_of"])
    diffs, n_orig, n_cand, sum_o, sum_c, dates = [], 0, 0, 0.0, 0.0, []
    for d in debates:
        if agent not in d["agents"]:
            continue
        layer = d["agents"][agent]["layer"]
        upstream = {k: v for k, v in d["agents"].items() if v["layer"] < layer}
        snap = bars.snapshot(d["as_of"])
        facts = features.build(snap, d["tickers"], cfg["benchmark"])
        out, _, _ = debate.run_agent(backend, home, agent, d["as_of"], d["tickers"], facts,
                                     upstream, prompt_text=candidate)
        orig = {r["ticker"]: r for r in d["agents"][agent]["recommendations"]}
        for rec in out["recommendations"]:
            a = scoring.score_rec(bars, orig[rec["ticker"]], d["as_of"], cfg["benchmark"])
            b = scoring.score_rec(bars, rec, d["as_of"], cfg["benchmark"])
            if a["status"] == "en attente" or b["status"] == "en attente":
                continue
            sa, sb = a["score"] or 0.0, b["score"] or 0.0
            diffs.append(sb - sa)
            sum_o += sa
            sum_c += sb
            n_orig += a["status"] == "note"
            n_cand += b["status"] == "note"
        dates.append(d["as_of"])
    st = paired_stats(diffs)
    verdict, why = decide(st, cfg["experiments"].get("min_validation_predictions", 30))
    original_path = debate.prompt_path(home, agent)
    if verdict == "KEEP":
        os.replace(cand_path, original_path)
    else:
        os.remove(cand_path)
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    entry = {"event": "decided", "agent": agent, "at": now, "verdict": verdict, "why": why,
             "validation_dates": [dates[0], dates[-1]] if dates else None, "debates": len(dates),
             "pairs": st["n"], "mean_diff": st["mean_diff"], "ci95": st["ci95"],
             "original_total": sum_o, "candidate_total": sum_c,
             "original_scored": n_orig, "candidate_scored": n_cand, "backend": backend.name}
    _append(home, entry)
    if git:
        entry["commit"] = commit(home, agent, verdict, why)
    return entry


def commit(home, agent, verdict, why):
    paths = [os.path.relpath(_log_path(home), home), os.path.relpath(debate.prompt_path(home, agent), home)]
    try:
        subprocess.run(["git", "add", "--"] + paths, cwd=home, check=True, capture_output=True, timeout=30)
        msg = "atlas-lab: %s %s\n\n%s" % (verdict, agent, why)
        subprocess.run(["git", "commit", "-m", msg, "--"] + paths, cwd=home, check=True,
                       capture_output=True, timeout=30)
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=home, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return "commit impossible: %s" % e
