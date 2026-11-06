"""One research session: the layers speak in order, everything is written down.

Layer 1 macro, 2 sectors, 3 investor styles, 4 risk then CIO. Each agent
gets the facts of the snapshot plus what the earlier layers said. The record
saved in runs/<date>/debate.json is the audit trail: data cutoff, data
fingerprint, prompt fingerprints, backend, and every claim with its sources.
"""

import datetime
import hashlib
import json
import os
import subprocess

from . import features, llm


class DebateError(Exception):
    pass


def prompt_path(home, agent):
    return os.path.join(home, "agents", agent + ".md")


def read_prompt(home, agent, candidate=False):
    path = prompt_path(home, agent)
    if candidate:
        path = path[:-3] + ".candidate.md"
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def git_commit(home):
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=home,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def roster(cfg):
    """[(layer, agent)] in speaking order; the CIO always speaks last."""
    out = []
    for layer, names in enumerate(cfg["layers"], start=1):
        for n in names:
            out.append((layer, n))
    return out


def payload_for(as_of, tickers, facts, upstream):
    return {"as_of": as_of, "tickers": list(tickers), "facts": facts,
            "upstream": {k: {"summary": v["summary"], "recommendations": v["recommendations"]}
                         for k, v in upstream.items()}}


def run_agent(backend, home, agent, as_of, tickers, facts, upstream, prompt_text=None):
    prompt_text = prompt_text if prompt_text is not None else read_prompt(home, agent)
    payload = payload_for(as_of, tickers, facts, upstream)
    raw, meta = backend.ask(agent, prompt_text, payload)
    out = llm.validate(raw, tickers)
    return out, meta, sha(prompt_text)


def run(bars, as_of, cfg, backend, home, save=True):
    if as_of not in bars.dates():
        raise DebateError("%s n'est pas un jour de bourse dans les donnees" % as_of)
    snap = bars.snapshot(as_of)
    if snap.last_date() > as_of:  # belt and braces: the guard itself is tested
        raise DebateError("fuite de donnees futures")
    tickers = cfg["tickers"]
    facts = features.build(snap, tickers, cfg["benchmark"])
    record = {
        "as_of": as_of,
        "data_cutoff": snap.last_date(),
        "data_source": bars.meta.get("source", "inconnue"),
        "data_fetched_at": bars.meta.get("fetched_at"),
        "data_fingerprint": snap.fingerprint(),
        "backend": backend.name,
        "model": getattr(backend, "model", None),
        "code_commit": git_commit(home),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tickers": list(tickers),
        "facts": facts,
        "agents": {},
        "paper_only": True,
    }
    upstream = {}
    for layer, agent in roster(cfg):
        out, meta, psha = run_agent(backend, home, agent, as_of, tickers, facts,
                                    {k: v for k, v in upstream.items()})
        record["agents"][agent] = {"layer": layer, "prompt_sha": psha, "meta": meta, **out}
        upstream[agent] = out
    if save:
        save_record(home, record)
    return record


def run_dir(home, as_of):
    return os.path.join(home, "runs", as_of)


def save_record(home, record):
    d = run_dir(home, record["as_of"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "debate.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)


def load_all(home):
    base = os.path.join(home, "runs")
    if not os.path.isdir(base):
        return []
    out = []
    for d in sorted(os.listdir(base)):
        p = os.path.join(base, d, "debate.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                out.append(json.load(fh))
    return out


def exists(home, as_of):
    return os.path.exists(os.path.join(run_dir(home, as_of), "debate.json"))
