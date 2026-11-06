"""Local dashboard: one static HTML file, no network, opens in any browser."""

import html
import json

E = html.escape


def _pct(x, k=1):
    return "n/d" if x is None else ("%+.*f %%" % (k, x * 100))


def _num(x, k=3):
    return "n/d" if x is None else "%.*f" % (k, x)


def equity_svg(equity, w=720, h=220):
    if len(equity) < 2:
        return "<p class='muted'>Pas encore de courbe (il faut au moins deux jours).</p>"
    vals = [e["equity"] for e in equity] + [e["benchmark"] for e in equity]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pad = 30

    def pts(key):
        n = len(equity) - 1
        return " ".join("%.1f,%.1f" % (pad + i * (w - 2 * pad) / n,
                                       h - pad - (e[key] - lo) / span * (h - 2 * pad))
                        for i, e in enumerate(equity))
    return (
        "<svg viewBox='0 0 %d %d' role='img' aria-label='Courbe du compte simule'>"
        "<polyline fill='none' stroke='var(--bench)' stroke-width='1.5' stroke-dasharray='4 3' points='%s'/>"
        "<polyline fill='none' stroke='var(--accent)' stroke-width='2' points='%s'/>"
        "<text x='%d' y='16' class='lbl'>%s</text><text x='%d' y='%d' class='lbl' dy='-6'>%s</text>"
        "<text x='%d' y='%d' class='lbl'>%s</text><text x='%d' y='%d' class='lbl' text-anchor='end'>%s</text>"
        "</svg><p class='legend'><span class='sw a'></span> compte simule "
        "<span class='sw b'></span> indice de reference (achat-conservation)</p>"
        % (w, h, pts("benchmark"), pts("equity"), pad, "%.0f $" % hi, pad, h - pad, "%.0f $" % lo,
           pad, h - pad + 14, equity[0]["date"], w - pad, h - pad + 14, equity[-1]["date"]))


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--card:#fff;--line:#e4e1d8;--accent:#2f6fdf;--bench:#9a968a;
--long:#1f8a4c;--short:#c0392b;--warn:#fff4d6;--warnfg:#7a5300;--mock:#ffe1e1;--mockfg:#8a1c1c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe6;--muted:#a3a197;
--card:#1f1f1d;--line:#34332f;--accent:#7aa7ff;--bench:#8b887d;--long:#5fd08e;--short:#ff8a7a;--warn:#3a2f10;
--warnfg:#ffd77a;--mock:#3d1717;--mockfg:#ffb3b3}}
:root[data-theme="dark"]{--bg:#161615;--fg:#ecebe6;--muted:#a3a197;--card:#1f1f1d;--line:#34332f;--accent:#7aa7ff;
--bench:#8b887d;--long:#5fd08e;--short:#ff8a7a;--warn:#3a2f10;--warnfg:#ffd77a;--mock:#3d1717;--mockfg:#ffb3b3}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}h1{font-size:24px;margin:8px 0}h2{font-size:18px;margin:28px 0 10px}
.banner{background:var(--warn);color:var(--warnfg);padding:10px 14px;border-radius:8px;font-weight:600}
.mock{background:var(--mock);color:var(--mockfg)}.muted{color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}
.kpi b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.card h3{margin:0 0 4px;font-size:15px}.tag{font-size:12px;color:var(--muted)}
.LONG{color:var(--long);font-weight:600}.SHORT{color:var(--short);font-weight:600}.NEUTRAL{color:var(--muted)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
svg{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
.lbl{fill:var(--muted);font-size:11px}.legend{font-size:13px;color:var(--muted)}
.sw{display:inline-block;width:14px;height:3px;vertical-align:middle;margin:0 4px 0 10px}.sw.a{background:var(--accent)}.sw.b{background:var(--bench)}
ul.small{font-size:13px;margin:4px 0;padding-left:18px}
"""


def render(debates, rows, card, sim, log, cfg):
    last = debates[-1] if debates else None
    source = last["data_source"] if last else "aucune"
    backend = last["backend"] if last else "aucun"
    p = ["<!doctype html><html lang='fr'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>ATLAS-lab</title><style>%s</style></head><body><main>" % CSS,
         "<h1>ATLAS-lab</h1>",
         "<p class='banner'>SIMULE. Papier uniquement: aucun ordre, aucun argent reel. "
         "Une performance simulee ne promet aucun gain.</p>"]
    if source == "mock" or backend == "mock":
        p.append("<p class='banner mock'>Mode demonstration: %s%s. Les chiffres ci-dessous testent la "
                 "machine, ils ne disent rien du marche.</p>"
                 % ("donnees FICTIVES" if source == "mock" else "donnees " + E(source),
                    ", agents remplaces par des regles fixes (mock)" if backend == "mock" else ""))
    p.append("<p class='muted'>Donnees: %s. Agents: %s. %d debats enregistres.</p>"
             % (E(source), E(backend), len(debates)))

    m = sim.get("metrics", {})
    p.append("<h2>Compte simule</h2>")
    if m:
        p.append("<div class='kpis'>")
        for label, val in (("Valeur finale", "%.0f $" % m["end_value"]),
                           ("Rendement simule", "%+.2f %%" % m["return_pct"]),
                           ("Indice de reference", "%+.2f %%" % m["benchmark_return_pct"]),
                           ("Baisse max", "%.2f %%" % m["max_drawdown_pct"]),
                           ("Frais payes", "%.2f $" % m["fees_usd"]),
                           ("Transactions", str(m["trades"])),
                           ("Sharpe", _num(m.get("sharpe"), 2) if m.get("sharpe") is not None else "trop tot")):
            p.append("<div class='kpi'><span class='muted'>%s</span><b>%s</b></div>" % (label, E(val)))
        p.append("</div>")
    p.append(equity_svg(sim.get("equity", [])))
    for w in sim.get("warnings", [])[:8]:
        p.append("<p class='muted'>Note: %s</p>" % E(w))

    if last:
        p.append("<h2>Dernier debat: %s</h2>" % E(last["as_of"]))
        p.append("<p class='muted'>Donnees visibles jusqu'au %s, empreinte %s.</p>"
                 % (E(last["data_cutoff"]), E(last["data_fingerprint"])))
        p.append("<div class='cards'>")
        for agent, out in sorted(last["agents"].items(), key=lambda kv: (kv[1]["layer"], kv[0])):
            p.append("<div class='card'><h3>%s</h3><span class='tag'>couche %d, prompt %s</span>"
                     "<p>%s</p><ul class='small'>" % (E(agent), out["layer"], E(out["prompt_sha"]), E(out["summary"])))
            for r in out["recommendations"]:
                p.append("<li><b>%s</b> <span class='%s'>%s</span> %d/100, %dj: %s</li>"
                         % (E(r["ticker"]), r["direction"], r["direction"], r["conviction"],
                            r["horizon_days"], E(r["claim"])))
            p.append("</ul></div>")
        p.append("</div>")

    p.append("<h2>Tableau de score par agent</h2><p class='muted'>Score = sens x (rendement - indice) x conviction. "
             "Positif = l'agent a vu juste.</p><div class='scroll'><table><tr><th>Agent</th><th>Periode</th>"
             "<th>Notees</th><th>Taux de reussite</th><th>Score moyen</th><th>Intervalle 95 %</th>"
             "<th>Sharpe</th><th>En attente</th></tr>")
    for agent in sorted(card):
        for split in ("train", "validation", "test", "live"):
            st = card[agent].get(split)
            if not st:
                continue
            ci = "n/d" if not st["ci95"] else "%s a %s" % (_num(st["ci95"][0], 4), _num(st["ci95"][1], 4))
            sh = _num(st["sharpe"], 2) if st["sharpe"] is not None else (st["note"] or "n/d")
            p.append("<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>"
                     % (E(agent), split, st["n"], _pct(st["hit_rate"], 0) if st["hit_rate"] is not None else "n/d",
                        _num(st["mean"], 4), ci, E(sh), st["pending"]))
    p.append("</table></div>")

    p.append("<h2>Predictions suivies (50 dernieres)</h2><div class='scroll'><table><tr><th>Date</th><th>Agent</th>"
             "<th>Ticker</th><th>Avis</th><th>Horizon</th><th>Statut</th><th>Ecart vs indice</th><th>Score</th></tr>")
    for r in sorted(rows, key=lambda r: (r["as_of"], r["layer"]), reverse=True)[:50]:
        p.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s %d</td><td>%dj</td><td>%s</td>"
                 "<td>%s</td><td>%s</td></tr>"
                 % (r["as_of"], E(r["agent"]), r["ticker"], r["direction"], r["direction"], r["conviction"],
                    r["horizon_days"], r["status"], _pct(r["excess"], 2), _num(r["score"], 4)))
    p.append("</table></div>")

    p.append("<h2>Experiences sur les prompts (GARDER / ANNULER)</h2>")
    if log:
        p.append("<div class='scroll'><table><tr><th>Date</th><th>Agent</th><th>Evenement</th><th>Detail</th></tr>")
        for e in log[-30:]:
            detail = e.get("why") or e.get("rationale") or ""
            if e.get("verdict"):
                detail = "%s (paires %s, ecart moyen %s)" % (detail, e.get("pairs"), _num(e.get("mean_diff"), 4))
            p.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (E(e["at"][:10]), E(e["agent"]), E(e.get("verdict") or e["event"]), E(detail)))
        p.append("</table></div>")
    else:
        p.append("<p class='muted'>Aucune experience pour l'instant.</p>")
    p.append("<p class='muted'>Projet educatif. Pas un conseil financier.</p></main></body></html>")
    return "\n".join(p)
