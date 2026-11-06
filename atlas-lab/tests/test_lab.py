"""Tests for atlas-lab. Run: python3 -m unittest discover -s atlas-lab/tests"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from atlas import dashboard, data, debate, experiment, features, llm, portfolio, scoring  # noqa: E402


def base_cfg():
    with open(os.path.join(LAB, "config.example.json")) as fh:
        return json.load(fh)


def make_home():
    home = tempfile.mkdtemp(prefix="atlas-test-")
    shutil.copytree(os.path.join(LAB, "agents"), os.path.join(home, "agents"))
    return home


def flat_bars(prices, symbols=("AAA", "SPY"), start="2025-01-06"):
    """prices: list of (open, close) per day, same for every symbol unless dict."""
    days = data.business_days(start, "2026-12-31")[:len(prices)]
    rows = []
    for d, p in zip(days, prices):
        for s in symbols:
            o, c = p[s] if isinstance(p, dict) else p
            rows.append({"date": d, "symbol": s, "open": o, "high": max(o, c), "low": min(o, c),
                         "close": c, "volume": 1.0})
    return data.Bars(rows, {"source": "test"}), days


class Data(unittest.TestCase):
    def test_mock_is_deterministic_and_labeled(self):
        a = data.mock_bars(["AAA", "SPY"], "2025-01-01", "2025-03-01")
        b = data.mock_bars(["AAA", "SPY"], "2025-01-01", "2025-03-01")
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertEqual(a.meta["source"], "mock")

    def test_snapshot_drops_the_future(self):
        bars = data.mock_bars(["AAA", "SPY"], "2025-01-01", "2025-03-01")
        snap = bars.snapshot("2025-02-03")
        self.assertEqual(snap.last_date(), "2025-02-03")
        self.assertTrue(all(r["date"] <= "2025-02-03" for r in snap.rows))

    def test_shift(self):
        bars, days = flat_bars([(1, 1)] * 5)
        self.assertEqual(bars.shift(days[0], 2), days[2])
        self.assertIsNone(bars.shift(days[4], 1))

    def test_alpaca_parsing_without_network(self):
        pages = [{"bars": {"AAA": [{"t": "2025-01-06T05:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}]},
                  "next_page_token": "p2"},
                 {"bars": {"AAA": [{"t": "2025-01-07T05:00:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 10}]},
                  "next_page_token": None}]
        seen = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.body).encode()

        def opener(req):
            seen.append(req)
            return Resp(pages[len(seen) - 1])

        bars = data.fetch_alpaca(["AAA", "SPY"], "2025-01-01", "2025-01-10", opener=opener,
                                 env={"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"})
        self.assertEqual(bars.dates(), ["2025-01-06", "2025-01-07"])
        self.assertEqual(bars.meta["missing_symbols"], ["SPY"])
        self.assertIn("page_token=p2", seen[1].full_url)
        self.assertTrue(seen[0].full_url.startswith("https://data.alpaca.markets/v2/stocks/bars"))

    def test_alpaca_needs_keys(self):
        with self.assertRaises(data.DataError):
            data.alpaca_keys({})


class NoLookAhead(unittest.TestCase):
    def test_changing_the_future_does_not_change_a_past_debate(self):
        cfg = base_cfg()
        home = make_home()
        bars = data.mock_bars(cfg["tickers"] + [cfg["benchmark"]], "2024-01-02", "2024-12-31")
        as_of = bars.dates()[100]
        a = debate.run(bars, as_of, cfg, llm.MockBackend(), home, save=False)
        future = [dict(r, close=r["close"] * (3 if r["date"] > as_of else 1)) for r in bars.rows]
        b = debate.run(data.Bars(future, bars.meta), as_of, cfg, llm.MockBackend(), home, save=False)
        self.assertEqual(a["agents"], b["agents"])
        self.assertEqual(a["data_fingerprint"], b["data_fingerprint"])
        self.assertEqual(a["data_cutoff"], as_of)

    def test_features_mark_stale_and_missing(self):
        bars, days = flat_bars([(1, 1)] * 3)
        rows = [r for r in bars.rows if not (r["symbol"] == "AAA" and r["date"] == days[2])]
        f = features.build(data.Bars(rows).snapshot(days[2]), ["AAA", "ZZZ"], "SPY")
        self.assertTrue(f["symbols"]["AAA"]["stale"])
        self.assertTrue(f["symbols"]["ZZZ"]["missing"])


class Audit(unittest.TestCase):
    def test_record_has_the_audit_fields_and_is_saved(self):
        cfg = base_cfg()
        home = make_home()
        bars = data.mock_bars(cfg["tickers"] + [cfg["benchmark"]], "2024-01-02", "2024-06-28")
        rec = debate.run(bars, bars.dates()[70], cfg, llm.MockBackend(), home)
        for k in ("as_of", "data_cutoff", "data_source", "data_fingerprint", "backend", "created_at", "facts"):
            self.assertIn(k, rec)
        self.assertTrue(rec["paper_only"])
        self.assertEqual([a for _, a in debate.roster(cfg)][-1], "cio")
        for out in rec["agents"].values():
            self.assertEqual(len(out["recommendations"]), len(cfg["tickers"]))
            for r in out["recommendations"]:
                self.assertTrue(r["sources"])
        self.assertTrue(debate.exists(home, rec["as_of"]))
        self.assertEqual(len(debate.load_all(home)), 1)

    def test_upstream_only_sees_earlier_layers(self):
        cfg = base_cfg()
        home = make_home()
        seen = {}

        class Spy(llm.MockBackend):
            def ask(self, agent, prompt, payload):
                seen[agent] = sorted(payload["upstream"])
                return super().ask(agent, prompt, payload)

        bars = data.mock_bars(cfg["tickers"] + [cfg["benchmark"]], "2024-01-02", "2024-06-28")
        debate.run(bars, bars.dates()[70], cfg, Spy(), home, save=False)
        self.assertEqual(seen["macro"], [])
        self.assertEqual(seen["secteurs"], ["macro"])
        self.assertEqual(seen["cio"], ["macro", "momentum", "qualite", "risque", "secteurs"])


class Validation(unittest.TestCase):
    def rec(self, **over):
        r = {"ticker": "AAA", "direction": "LONG", "conviction": 60, "horizon_days": 5,
             "claim": "x", "scenario": "y", "sources": ["bars:AAA@d"]}
        r.update(over)
        return {"summary": "s", "recommendations": [r]}

    def test_good_output_passes(self):
        self.assertEqual(llm.validate(self.rec(direction="long"), ["AAA"])["recommendations"][0]["direction"], "LONG")

    def test_bad_outputs_are_rejected(self):
        for bad in (dict(ticker="ZZZ"), dict(direction="BUY"), dict(conviction=140),
                    dict(horizon_days=3), dict(conviction="beaucoup")):
            with self.assertRaises(llm.BackendError, msg=str(bad)):
                llm.validate(self.rec(**bad), ["AAA"])
        with self.assertRaises(llm.BackendError):
            llm.validate(self.rec(), ["AAA", "BBB"])  # missing BBB

    def test_extract_json_from_a_chatty_reply(self):
        text = 'Voici:\n```json\n{"summary": "a {b}", "recommendations": []}\n```\nfin'
        self.assertEqual(llm.extract_json(text)["summary"], "a {b}")


class Backends(unittest.TestCase):
    payload = {"as_of": "2025-01-06", "tickers": ["AAA"], "facts": {}, "upstream": {}}
    answer = {"summary": "ok", "recommendations": []}

    def test_claude_code_runs_without_tools(self):
        calls = []

        def runner(cmd, **kw):
            calls.append((cmd, kw))
            out = json.dumps({"result": json.dumps(self.answer), "total_cost_usd": 0.01})
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

        be = llm.ClaudeCodeBackend(runner=runner)
        out, meta = be.ask("macro", "prompt", self.payload)
        self.assertEqual(out["summary"], "ok")
        cmd = calls[0][0]
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertIn("2025-01-06", cmd[cmd.index("--system-prompt") + 1])
        self.assertEqual(meta["cost_usd"], 0.01)

    def test_claude_code_failure_is_reported(self):
        be = llm.ClaudeCodeBackend(runner=lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
        with self.assertRaises(llm.BackendError):
            be.ask("macro", "p", self.payload)

    def test_api_request_shape(self):
        sent = {}
        answer = self.answer

        class Msgs:
            def create(self, **kw):
                sent.update(kw)
                return types.SimpleNamespace(
                    stop_reason="end_turn",
                    content=[types.SimpleNamespace(type="text", text=json.dumps(answer))],
                    usage=types.SimpleNamespace(input_tokens=10, output_tokens=5))

        client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=Msgs()))
        out, meta = llm.ApiBackend(client=client).ask("macro", "prompt", self.payload)
        self.assertEqual(out["summary"], "ok")
        self.assertEqual(sent["model"], "claude-opus-5-5")
        self.assertEqual(sent["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(sent["thinking"], {"type": "adaptive"})
        self.assertEqual(meta["input_tokens"], 10)

    def test_api_refusal_raises(self):
        class Msgs:
            def create(self, **kw):
                return types.SimpleNamespace(stop_reason="refusal", content=[], usage=None)
        client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=Msgs()))
        with self.assertRaises(llm.BackendError):
            llm.ApiBackend(client=client).ask("macro", "p", self.payload)


class Scoring(unittest.TestCase):
    def test_forward_excess_return_by_hand(self):
        # AAA +10% over 2 days, SPY +5%: LONG 50 scores (0.10-0.05)*0.5 = 0.025
        prices = [{"AAA": (100, 100), "SPY": (100, 100)}, {"AAA": (100, 105), "SPY": (100, 102)},
                  {"AAA": (100, 110), "SPY": (100, 105)}]
        bars, days = flat_bars(prices)
        rec = {"ticker": "AAA", "direction": "LONG", "conviction": 50, "horizon_days": 1}
        self.assertEqual(scoring.score_rec(bars, dict(rec, horizon_days=5), days[0], "SPY")["status"], "en attente")
        r = scoring.score_rec(bars, dict(rec, horizon_days=1), days[1], "SPY")
        self.assertAlmostEqual(r["excess"], 110 / 105 - 105 / 102)
        bars2, d2 = flat_bars(prices)
        r2 = scoring.forward_return(bars2, "AAA", d2[0], 2)
        self.assertAlmostEqual(r2, 0.10)
        short = scoring.score_rec(bars, dict(rec, direction="SHORT"), days[1], "SPY")
        self.assertAlmostEqual(short["score"], -r["score"])
        self.assertFalse(short["hit"])

    def test_no_sharpe_on_a_small_sample(self):
        st = scoring.stats([0.01, -0.02, 0.03], 5, 30)
        self.assertIsNone(st["sharpe"])
        self.assertIn("trop petit", st["note"])
        st = scoring.stats([0.01, 0.02] * 20, 5, 30)
        self.assertIsNotNone(st["sharpe"])

    def test_splits(self):
        cfg = base_cfg()
        self.assertEqual(scoring.split_of("2024-06-01", cfg), "train")
        self.assertEqual(scoring.split_of("2025-03-01", cfg), "validation")
        self.assertEqual(scoring.split_of("2025-09-01", cfg), "test")
        self.assertEqual(scoring.split_of("2026-11-06", cfg), "live")


class Simulator(unittest.TestCase):
    def cfg(self):
        c = base_cfg()
        c["simulation"].update(slippage_bps=100, fee_bps=10, min_fee_usd=1.0, starting_cash=1000,
                               min_trade_usd=1, min_days_for_sharpe=60)
        c["max_position_pct"] = 50
        c["cash_buffer_pct"] = 10
        return c

    def debate_on(self, day, direction="LONG", conv=80):
        return {"as_of": day, "agents": {"cio": {"layer": 5, "recommendations": [
            {"ticker": "AAA", "direction": direction, "conviction": conv, "horizon_days": 5}]}}}

    def test_next_open_fill_with_slippage_fees_and_cap(self):
        prices = [(100, 100), (100, 110), (120, 90)]
        bars, days = flat_bars(prices)
        res = portfolio.simulate(bars, [self.debate_on(days[0])], self.cfg())
        t = res["trades"][0]
        self.assertEqual(t["date"], days[1])            # decided day 0, filled at day-1 open
        self.assertAlmostEqual(t["price"], 101.0)       # open 100 + 1% slippage
        qty = 0.5 * 1000 / 100                          # capped at 50%
        self.assertAlmostEqual(t["qty"], qty, places=5)
        fee = max(1.0, qty * 101 * 0.001)
        self.assertAlmostEqual(t["fee"], round(fee, 2))
        cash = 1000 - qty * 101 - fee
        self.assertAlmostEqual(res["equity"][0]["equity"], round(cash + qty * 110, 2))
        self.assertAlmostEqual(res["equity"][1]["equity"], round(cash + qty * 90, 2))
        self.assertLess(res["metrics"]["max_drawdown_pct"], 0)
        self.assertIsNone(res["metrics"]["sharpe"])
        self.assertTrue(all(t["simulated"] for t in res["trades"]))

    def test_neutral_cio_means_sell(self):
        prices = [(100, 100)] * 5
        bars, days = flat_bars(prices)
        res = portfolio.simulate(bars, [self.debate_on(days[0]), self.debate_on(days[2], "NEUTRAL")], self.cfg())
        self.assertEqual([t["side"] for t in res["trades"]], ["achat", "vente"])

    def test_long_only_and_no_trade_without_debates(self):
        bars, days = flat_bars([(100, 100)] * 4)
        res = portfolio.simulate(bars, [self.debate_on(days[0], "SHORT", 90)], self.cfg())
        self.assertEqual(res["trades"], [])
        self.assertIn("aucun debat", " ".join(portfolio.simulate(bars, [], self.cfg())["warnings"]))


class Experiments(unittest.TestCase):
    def test_decide(self):
        self.assertEqual(experiment.decide({"n": 5, "ci95": [0.1, 0.2]}, 30)[0], "REVERT")
        self.assertEqual(experiment.decide({"n": 40, "ci95": [-0.01, 0.2]}, 30)[0], "REVERT")
        self.assertEqual(experiment.decide({"n": 40, "ci95": [0.001, 0.2]}, 30)[0], "KEEP")

    def setUp(self):
        self.cfg = base_cfg()
        self.home = make_home()
        self.bars = data.mock_bars(self.cfg["tickers"] + [self.cfg["benchmark"]], "2024-01-02", "2025-12-31")
        for d in self.bars.dates()[60::5]:
            debate.run(self.bars, d, self.cfg, llm.MockBackend(), self.home)
        subprocess.run(["git", "init", "-q"], cwd=self.home, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], cwd=self.home, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                       cwd=self.home, check=True)

    def test_propose_then_revert_is_logged_and_committed(self):
        e = experiment.propose(self.home, self.bars, self.cfg, llm.MockBackend())
        agent = e["agent"]
        self.assertTrue(os.path.exists(os.path.join(self.home, "agents", agent + ".candidate.md")))
        with self.assertRaises(experiment.ExperimentError):
            experiment.propose(self.home, self.bars, self.cfg, llm.MockBackend(), agent=agent)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        old = dict(os.environ)
        os.environ.update(env)
        try:
            d = experiment.evaluate(self.home, self.bars, self.cfg, llm.MockBackend(), agent)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(d["verdict"], "REVERT")  # mock ignores its prompt: no gain
        self.assertFalse(os.path.exists(os.path.join(self.home, "agents", agent + ".candidate.md")))
        logmsg = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=self.home, capture_output=True, text=True)
        self.assertEqual(logmsg.stdout.strip(), "atlas-lab: REVERT %s" % agent)
        self.assertEqual([x["event"] for x in experiment.read_log(self.home)], ["proposed", "decided"])

    def test_evaluate_uses_validation_only(self):
        e = experiment.propose(self.home, self.bars, self.cfg, llm.MockBackend())
        seen = []

        class Spy(llm.MockBackend):
            def ask(self, agent, prompt, payload):
                seen.append(payload["as_of"])
                return super().ask(agent, prompt, payload)

        experiment.evaluate(self.home, self.bars, self.cfg, Spy(), e["agent"], git=False)
        self.assertTrue(seen)
        self.assertTrue(all(scoring.split_of(d, self.cfg) == "validation" for d in seen))

    def test_keep_replaces_the_prompt(self):
        cfg = self.cfg
        e = experiment.propose(self.home, self.bars, cfg, llm.MockBackend(), agent="momentum")

        class Better(llm.MockBackend):
            """Cheats with the future on purpose, only to force a measurable gain in this test."""
            def __init__(self, bars):
                self.bars = bars

            def ask(self, agent, prompt, payload):
                out, meta = super().ask(agent, prompt, payload)
                for r in out["recommendations"]:
                    res = scoring.score_rec(self.bars, dict(r, direction="LONG", conviction=100),
                                            payload["as_of"], cfg["benchmark"])
                    if res["excess"] is not None:
                        r["direction"] = "LONG" if res["excess"] > 0 else "SHORT"
                        r["conviction"] = 100
                return out, meta

        d = experiment.evaluate(self.home, self.bars, cfg, Better(self.bars), "momentum", git=False)
        self.assertEqual(d["verdict"], "KEEP")
        self.assertIn("Regle ajoutee (mock)", debate.read_prompt(self.home, "momentum"))


class Dashboard(unittest.TestCase):
    def test_renders_labels_and_no_em_dash(self):
        cfg = base_cfg()
        home = make_home()
        bars = data.mock_bars(cfg["tickers"] + [cfg["benchmark"]], "2024-01-02", "2024-12-31")
        ds = [debate.run(bars, d, cfg, llm.MockBackend(), home) for d in bars.dates()[60::20]]
        rows = scoring.predictions(bars, ds, cfg)
        page = dashboard.render(ds, rows, scoring.scorecard(rows, cfg), portfolio.simulate(bars, ds, cfg), [], cfg)
        self.assertIn("SIMULE", page)
        self.assertIn("FICTIVES", page)
        self.assertNotIn("\u2014", page)
        self.assertIn("<svg", page)


class Safety(unittest.TestCase):
    def test_no_order_endpoint_anywhere(self):
        for base, _, files in os.walk(LAB):
            for f in files:
                if f.endswith(".py") and f != "test_lab.py":
                    with open(os.path.join(base, f)) as fh:
                        text = fh.read()
                    self.assertNotIn("/v2/orders", text, f)
                    self.assertNotIn("api.alpaca.markets", text, f)
                    self.assertNotIn("submit_order", text, f)

    def test_config_is_paper_only(self):
        self.assertIs(base_cfg()["paper_only"], True)


if __name__ == "__main__":
    unittest.main()
