"""Market data for atlas-lab: daily bars, timestamped, with a look-ahead guard.

Bars live in data/bars.csv (date,symbol,open,high,low,close,volume) and
data/meta.json says where they came from (alpaca-iex or mock) and when.

Agents never see a Bars object directly. They see bars.snapshot(as_of),
which drops every row after as_of. That is the only door to the data.
"""

import bisect
import csv
import datetime
import hashlib
import json
import math
import os
import random
import urllib.parse
import urllib.request

FIELDS = ("date", "symbol", "open", "high", "low", "close", "volume")


class DataError(Exception):
    pass


class Bars:
    def __init__(self, rows, meta=None):
        self.rows = sorted(rows, key=lambda r: (r["date"], r["symbol"]))
        self.meta = dict(meta or {})
        self._by = {}
        for r in self.rows:
            self._by[(r["symbol"], r["date"])] = r
        self._dates = sorted({r["date"] for r in self.rows})
        self._pos = {d: i for i, d in enumerate(self._dates)}

    # ----- loading / saving
    @classmethod
    def load(cls, folder):
        path = os.path.join(folder, "bars.csv")
        if not os.path.exists(path):
            raise DataError("pas de donnees: %s (lance `python3 lab.py mock-data` ou `fetch`)" % path)
        rows = []
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append({"date": r["date"], "symbol": r["symbol"],
                             "open": float(r["open"]), "high": float(r["high"]),
                             "low": float(r["low"]), "close": float(r["close"]),
                             "volume": float(r["volume"])})
        meta = {}
        mpath = os.path.join(folder, "meta.json")
        if os.path.exists(mpath):
            with open(mpath) as fh:
                meta = json.load(fh)
        return cls(rows, meta)

    def save(self, folder):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "bars.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for r in self.rows:
                w.writerow({k: r[k] for k in FIELDS})
        with open(os.path.join(folder, "meta.json"), "w") as fh:
            json.dump(self.meta, fh, indent=2)

    # ----- access
    def dates(self):
        return list(self._dates)

    def symbols(self):
        return sorted({r["symbol"] for r in self.rows})

    def last_date(self):
        return self._dates[-1] if self._dates else None

    def get(self, symbol, date):
        return self._by.get((symbol, date))

    def close(self, symbol, date):
        r = self.get(symbol, date)
        return r["close"] if r else None

    def open(self, symbol, date):
        r = self.get(symbol, date)
        return r["open"] if r else None

    def series(self, symbol):
        return [(r["date"], r["close"]) for r in self.rows if r["symbol"] == symbol]

    def shift(self, date, n):
        """Trading date n sessions after (n>0) or before (n<0) date, or None."""
        i = self._pos.get(date)
        if i is None:
            i = bisect.bisect_right(self._dates, date) - 1
        j = i + n
        if 0 <= j < len(self._dates):
            return self._dates[j]
        return None

    def snapshot(self, as_of):
        """Everything known at the close of as_of, nothing after."""
        rows = [r for r in self.rows if r["date"] <= as_of]
        meta = dict(self.meta)
        meta["snapshot_as_of"] = as_of
        return Bars(rows, meta)

    def fingerprint(self):
        h = hashlib.sha256()
        for r in self.rows:
            h.update(("%s,%s,%.6f,%.6f\n" % (r["date"], r["symbol"], r["open"], r["close"])).encode())
        return h.hexdigest()[:16]


# ----- mock data (clearly labeled, for tests and the free demo)

def business_days(start, end):
    d = datetime.date.fromisoformat(start)
    stop = datetime.date.fromisoformat(end)
    out = []
    while d <= stop:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def mock_bars(symbols, start, end, seed=7):
    """Deterministic random walks. NOT market data: meta.source says mock."""
    rng = random.Random(seed)
    days = business_days(start, end)
    rows = []
    for k, sym in enumerate(sorted(symbols)):
        price = 50.0 + 25.0 * k
        drift = rng.uniform(-0.0002, 0.0006)
        vol = rng.uniform(0.010, 0.022)
        for d in days:
            ret = drift + vol * rng.gauss(0, 1)
            op = price * (1 + vol * 0.3 * rng.gauss(0, 1))
            cl = max(1.0, price * math.exp(ret))
            hi = max(op, cl) * (1 + abs(rng.gauss(0, vol * 0.5)))
            lo = min(op, cl) * (1 - abs(rng.gauss(0, vol * 0.5)))
            rows.append({"date": d, "symbol": sym, "open": round(op, 4), "high": round(hi, 4),
                         "low": round(lo, 4), "close": round(cl, 4),
                         "volume": float(rng.randint(1_000_000, 9_000_000))})
            price = cl
    meta = {"source": "mock", "warning": "DONNEES FICTIVES, pas des vrais prix",
            "symbols": sorted(symbols), "start": start, "end": end, "seed": seed,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    return Bars(rows, meta)


# ----- Alpaca market data (read only: this module has no order endpoint)

ALPACA_BARS = "https://data.alpaca.markets/v2/stocks/bars"


def alpaca_keys(env=None):
    env = os.environ if env is None else env
    key = env.get("ALPACA_API_KEY") or env.get("APCA_API_KEY_ID")
    secret = env.get("ALPACA_SECRET_KEY") or env.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise DataError("cles Alpaca absentes: mets ALPACA_API_KEY et ALPACA_SECRET_KEY "
                        "dans l'environnement (jamais dans Git ni dans le chat)")
    return key, secret


def fetch_alpaca(symbols, start, end, feed="iex", opener=None, env=None):
    """Daily bars from Alpaca's market-data API (free IEX feed by default)."""
    key, secret = alpaca_keys(env)
    opener = opener or urllib.request.urlopen
    rows, token = [], None
    while True:
        q = {"symbols": ",".join(sorted(symbols)), "timeframe": "1Day", "start": start,
             "end": end, "feed": feed, "adjustment": "all", "limit": "10000"}
        if token:
            q["page_token"] = token
        req = urllib.request.Request(ALPACA_BARS + "?" + urllib.parse.urlencode(q), headers={
            "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"})
        with opener(req) as resp:
            body = json.loads(resp.read().decode())
        for sym, bars in (body.get("bars") or {}).items():
            for b in bars:
                rows.append({"date": b["t"][:10], "symbol": sym, "open": float(b["o"]),
                             "high": float(b["h"]), "low": float(b["l"]), "close": float(b["c"]),
                             "volume": float(b["v"])})
        token = body.get("next_page_token")
        if not token:
            break
    if not rows:
        raise DataError("Alpaca n'a renvoye aucune barre pour %s" % ",".join(symbols))
    meta = {"source": "alpaca-" + feed, "symbols": sorted(symbols), "start": start, "end": end,
            "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    missing = sorted(set(symbols) - {r["symbol"] for r in rows})
    if missing:
        meta["missing_symbols"] = missing
    return Bars(rows, meta)
