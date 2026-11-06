#!/usr/bin/env python3
"""ATLAS-lab: paper-only multi-agent research. Standard library only
(the optional `api` backend needs `pip install anthropic`).

    python3 lab.py demo                       # free: mock data + mock agents, end to end
    python3 lab.py fetch                      # daily bars from Alpaca (read only)
    python3 lab.py mock-data                  # fictitious bars instead
    python3 lab.py debate --date 2025-03-03   # one session of the team
    python3 lab.py backtest                   # a debate every N days over the data
    python3 lab.py report                     # scorecard + simulation + dashboard
    python3 lab.py daily                      # after the close: fetch, debate, report
    python3 lab.py experiment propose [--agent X]
    python3 lab.py experiment evaluate --agent X

Add --backend mock|claude-code|api to override the config. Nothing here
can place an order: there is no order endpoint in this folder.
"""

import argparse
import datetime
import json
import os
import sys

HOME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOME)

from atlas import dashboard, data, debate, experiment, llm, portfolio, scoring  # noqa: E402


def load_cfg(home):
    path = os.path.join(home, "config.json")
    if not os.path.exists(path):
        path = os.path.join(home, "config.example.json")
    with open(path) as fh:
        cfg = json.load(fh)
    if cfg.get("paper_only") is not True:
        raise SystemExit("paper_only doit rester true: ce laboratoire ne trade pas.")
    return cfg


def data_dir(home):
    return os.path.join(home, "data")


def cmd_mock_data(home, cfg, args):
    d = cfg["data"]
    bars = data.mock_bars(cfg["tickers"] + [cfg["benchmark"]], d["start"], d["end"])
    bars.save(data_dir(home))
    print("donnees FICTIVES ecrites: %d barres, %s a %s" % (len(bars.rows), d["start"], d["end"]))


def cmd_fetch(home, cfg, args):
    d = cfg["data"]
    end = args.end or d.get("end") or datetime.date.today().isoformat()
    bars = data.fetch_alpaca(cfg["tickers"] + [cfg["benchmark"]], args.start or d["start"], end, d.get("feed", "iex"))
    bars.save(data_dir(home))
    print("Alpaca: %d barres, dernier jour %s" % (len(bars.rows), bars.last_date()))
    if bars.meta.get("missing_symbols"):
        print("ATTENTION: pas de donnees pour", ", ".join(bars.meta["missing_symbols"]))


def cmd_debate(home, cfg, args):
    bars = data.Bars.load(data_dir(home))
    as_of = args.date or bars.last_date()
    rec = debate.run(bars, as_of, cfg, llm.make(cfg), home)
    cio = rec["agents"]["cio"]
    print("debat du %s enregistre (runs/%s/debate.json)" % (as_of, as_of))
    print("CIO:", cio["summary"])
    for r in sorted(cio["recommendations"], key=lambda r: -r["conviction"]):
        print("  %-6s %-7s %3d/100  %s" % (r["ticker"], r["direction"], r["conviction"], r["claim"]))


def cmd_backtest(home, cfg, args):
    bars = data.Bars.load(data_dir(home))
    backend = llm.make(cfg)
    dates = bars.dates()
    warm = 60  # enough history for the 60-day facts
    start = args.start or dates[min(warm, len(dates) - 1)]
    end = args.end or dates[-1]
    every = args.every or cfg.get("debate_every_n_days", 5)
    todo = [d for d in dates if start <= d <= end][::every]
    done = 0
    for d in todo:
        if scoring.split_of(d, cfg) == "test" and not args.include_test:
            continue
        if debate.exists(home, d) and not args.redo:
            continue
        debate.run(bars, d, cfg, backend, home)
        done += 1
        print("debat %s (%s)" % (d, scoring.split_of(d, cfg)))
    print("%d nouveaux debats (backend %s)." % (done, backend.name))
    if not args.include_test:
        print("La periode de test est laissee intacte. Ajoute --include-test une seule fois, a la fin.")


def report(home, cfg):
    bars = data.Bars.load(data_dir(home))
    debates = debate.load_all(home)
    rows = scoring.predictions(bars, debates, cfg)
    card = scoring.scorecard(rows, cfg)
    sim = portfolio.simulate(bars, debates, cfg)
    log = experiment.read_log(home)
    os.makedirs(os.path.join(home, "out"), exist_ok=True)
    with open(os.path.join(home, "out", "scorecard.json"), "w") as fh:
        json.dump(card, fh, indent=1)
    with open(os.path.join(home, "out", "simulation.json"), "w") as fh:
        json.dump(sim, fh, indent=1)
    path = os.path.join(home, "out", "dashboard.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dashboard.render(debates, rows, card, sim, log, cfg))
    return path, card, sim


def cmd_report(home, cfg, args):
    path, card, sim = report(home, cfg)
    m = sim.get("metrics") or {}
    if m:
        print("SIMULE: %+.2f %% (indice %+.2f %%), baisse max %.2f %%, %d transactions"
              % (m["return_pct"], m["benchmark_return_pct"], m["max_drawdown_pct"], m["trades"]))
    for agent in sorted(card):
        for split, st in sorted(card[agent].items()):
            print("  %-9s %-10s n=%-4d moyenne=%s %s" % (agent, split, st["n"],
                  "n/d" if st["mean"] is None else "%.4f" % st["mean"], st["note"] or ""))
    print("tableau de bord:", path)


def cmd_daily(home, cfg, args):
    if not args.skip_fetch:
        cmd_fetch(home, cfg, argparse.Namespace(start=None, end=datetime.date.today().isoformat()))
    cmd_debate(home, cfg, argparse.Namespace(date=None))
    cmd_report(home, cfg, args)


def cmd_demo(home, cfg, args):
    cfg = dict(cfg, backend="mock")
    cmd_mock_data(home, cfg, args)
    cmd_backtest(home, cfg, argparse.Namespace(start=None, end=None, every=None, include_test=False, redo=True))
    cmd_report(home, cfg, args)


def cmd_experiment(home, cfg, args):
    bars = data.Bars.load(data_dir(home))
    backend = llm.make(cfg)
    if args.action == "propose":
        e = experiment.propose(home, bars, cfg, backend, agent=args.agent)
        print("candidat pour %s: agents/%s.candidate.md" % (e["agent"], e["agent"]))
        print("raison:", e["rationale"])
    else:
        if not args.agent:
            raise SystemExit("--agent requis")
        e = experiment.evaluate(home, bars, cfg, backend, args.agent, git=not args.no_git)
        print("%s %s: %s" % (e["verdict"], e["agent"], e["why"]))
        print("paires %s, ecart moyen %s, intervalle %s" % (e["pairs"], e["mean_diff"], e["ci95"]))
        if e.get("commit"):
            print("commit:", e["commit"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="ATLAS-lab, papier uniquement")
    ap.add_argument("--home", default=HOME)
    ap.add_argument("--backend", choices=["mock", "claude-code", "api"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mock-data")
    f = sub.add_parser("fetch")
    f.add_argument("--start")
    f.add_argument("--end")
    d = sub.add_parser("debate")
    d.add_argument("--date")
    b = sub.add_parser("backtest")
    b.add_argument("--start")
    b.add_argument("--end")
    b.add_argument("--every", type=int)
    b.add_argument("--include-test", action="store_true")
    b.add_argument("--redo", action="store_true")
    sub.add_parser("report")
    dl = sub.add_parser("daily")
    dl.add_argument("--skip-fetch", action="store_true")
    sub.add_parser("demo")
    e = sub.add_parser("experiment")
    e.add_argument("action", choices=["propose", "evaluate"])
    e.add_argument("--agent")
    e.add_argument("--no-git", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_cfg(args.home)
    if args.backend:
        cfg["backend"] = args.backend
    fn = {"mock-data": cmd_mock_data, "fetch": cmd_fetch, "debate": cmd_debate, "backtest": cmd_backtest,
          "report": cmd_report, "daily": cmd_daily, "demo": cmd_demo, "experiment": cmd_experiment}[args.cmd]
    try:
        fn(args.home, cfg, args)
    except (data.DataError, llm.BackendError, debate.DebateError, experiment.ExperimentError) as err:
        raise SystemExit("erreur: %s" % err)


if __name__ == "__main__":
    main()
