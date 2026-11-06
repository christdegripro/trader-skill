"""Who speaks for the agents. Three backends, same contract.

    mock         rule-based stand-in, free, deterministic. NOT an AI: for tests
                 and to check the plumbing. Its records say backend=mock.
    claude-code  runs `claude -p` on your machine (your Claude Code login,
                 no API key). Tools are disabled: the agent can only read the
                 prompt it is given.
    api          the Anthropic API with your ANTHROPIC_API_KEY (paid).

Every backend returns the same dict, checked by validate():
    {"summary": str, "recommendations": [{"ticker", "direction",
     "conviction", "horizon_days", "claim", "scenario", "sources"}]}
"""

import json
import re
import shutil
import subprocess

DIRECTIONS = ("LONG", "SHORT", "NEUTRAL")
HORIZONS = (1, 5, 20)

REC_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "direction": {"type": "string", "enum": list(DIRECTIONS)},
        "conviction": {"type": "integer"},
        "horizon_days": {"type": "integer", "enum": list(HORIZONS)},
        "claim": {"type": "string"},
        "scenario": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ticker", "direction", "conviction", "horizon_days", "claim", "scenario", "sources"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"},
                   "recommendations": {"type": "array", "items": REC_SCHEMA}},
    "required": ["summary", "recommendations"],
    "additionalProperties": False,
}

RULES = """
Regles communes (non negociables):
- Tu fais de la recherche papier. Aucun ordre n'est jamais passe.
- Utilise UNIQUEMENT les donnees fournies dans le message. Nous sommes a la cloture du {as_of}.
  N'utilise rien de ce que tu sais sur des evenements posterieurs a cette date.
- Si une donnee manque ou est marquee stale/missing, dis-le et baisse ta conviction.
- Donne exactement une recommandation par ticker de la liste: {tickers}.
- direction: LONG, SHORT ou NEUTRAL. conviction: entier de 0 a 100. horizon_days: 1, 5 ou 20.
- sources: les etiquettes "source" des donnees que tu as utilisees (ex: bars:AAPL@{as_of}),
  ou le nom d'un agent amont (ex: agent:macro).
- Reponds par un seul objet JSON: {{"summary": "...", "recommendations": [...]}} et rien d'autre.
"""


class BackendError(Exception):
    pass


def validate(out, tickers):
    """Normalise one agent output or raise BackendError. Never invents values."""
    if not isinstance(out, dict) or not isinstance(out.get("recommendations"), list):
        raise BackendError("sortie sans liste recommendations")
    seen, recs = set(), []
    for r in out["recommendations"]:
        if not isinstance(r, dict):
            raise BackendError("recommandation illisible: %r" % (r,))
        t = str(r.get("ticker", "")).upper()
        if t not in tickers:
            raise BackendError("ticker hors liste: %s" % t)
        if t in seen:
            raise BackendError("ticker en double: %s" % t)
        d = str(r.get("direction", "")).upper()
        if d not in DIRECTIONS:
            raise BackendError("direction invalide pour %s: %s" % (t, d))
        try:
            c = int(r.get("conviction"))
            h = int(r.get("horizon_days"))
        except (TypeError, ValueError):
            raise BackendError("conviction ou horizon non numerique pour %s" % t)
        if not 0 <= c <= 100:
            raise BackendError("conviction hors 0-100 pour %s: %s" % (t, c))
        if h not in HORIZONS:
            raise BackendError("horizon invalide pour %s: %s" % (t, h))
        src = r.get("sources") or []
        if not isinstance(src, list):
            src = [str(src)]
        seen.add(t)
        recs.append({"ticker": t, "direction": d, "conviction": c, "horizon_days": h,
                     "claim": str(r.get("claim", "")), "scenario": str(r.get("scenario", "")),
                     "sources": [str(s) for s in src]})
    missing = sorted(set(tickers) - seen)
    if missing:
        raise BackendError("tickers sans avis: %s" % ",".join(missing))
    return {"summary": str(out.get("summary", "")), "recommendations": recs}


def extract_json(text):
    """First JSON object in a model reply (tolerates ```json fences)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise BackendError("pas de JSON dans la reponse")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise BackendError("JSON incomplet dans la reponse")


def system_prompt(agent_prompt, as_of, tickers):
    return agent_prompt.strip() + "\n" + RULES.format(as_of=as_of, tickers=", ".join(tickers))


# ---------------------------------------------------------------- mock

class MockBackend:
    """Deterministic rules on the facts. A stand-in, not an analyst."""
    name = "mock"
    model = "regles-fictives"

    def ask(self, agent, agent_prompt, payload):
        tickers = payload["tickers"]
        facts = payload["facts"]["symbols"]
        bench = facts.get(payload["facts"]["benchmark"], {})
        b20 = bench.get("ret_20d") or 0.0
        recs = []
        for t in tickers:
            f = facts.get(t, {})
            r20 = f.get("ret_20d") or 0.0
            r5 = f.get("ret_5d") or 0.0
            vol = f.get("vol_20d_annual") or 0.3
            dd = f.get("drawdown_from_60d_high") or 0.0
            if agent == "macro":
                score = b20 * 10
            elif agent == "secteurs":
                score = (r20 - b20) * 8
            elif agent == "momentum":
                score = r20 * 6 + r5 * 4
            elif agent == "qualite":
                score = 0.4 - vol + dd * -1.5 if dd > -0.15 else -0.3
            elif agent == "risque":
                score = -0.5 if vol > 0.35 or dd < -0.12 else 0.1
            elif agent == "cio":
                up = payload.get("upstream", {})
                total = 0.0
                for out in up.values():
                    for rec in out["recommendations"]:
                        if rec["ticker"] == t:
                            sign = {"LONG": 1, "SHORT": -1}.get(rec["direction"], 0)
                            total += sign * rec["conviction"] / 100
                score = total / max(1, len(up))
            else:
                score = 0.0
            if score > 0.05:
                d = "LONG"
            elif score < -0.05:
                d = "SHORT"
            else:
                d = "NEUTRAL"
            conv = int(max(0, min(100, 50 + abs(score) * 100)))
            recs.append({"ticker": t, "direction": d, "conviction": conv,
                         "horizon_days": 5 if agent in ("momentum", "risque") else 20,
                         "claim": "[mock] %s: score %.3f (ret20 %.3f, vol %.2f)" % (agent, score, r20, vol),
                         "scenario": "[mock] regle fixe, pas une analyse",
                         "sources": [f.get("source", "")] if agent != "cio" else
                         ["agent:" + a for a in sorted(payload.get("upstream", {}))]})
        return {"summary": "[mock] %s, %d avis par regles fixes" % (agent, len(recs)),
                "recommendations": recs}, {"cost_usd": 0.0}

    def revise(self, agent, prompt, mistakes):
        rule = "\n- Regle ajoutee (mock): baisse ta conviction de moitie quand vol_20d_annual > 0.35.\n"
        return {"rationale": "[mock] erreurs frequentes en forte volatilite", "new_prompt": prompt.rstrip() + "\n" + rule}


# ---------------------------------------------------------------- claude code

class ClaudeCodeBackend:
    """`claude -p` on this machine. Uses your Claude Code login, no API key."""
    name = "claude-code"

    def __init__(self, model=None, runner=None, timeout=900):
        self.model = model or "defaut-claude-code"
        self._model_flag = model
        self.runner = runner or subprocess.run
        self.timeout = timeout
        if runner is None and not shutil.which("claude"):
            raise BackendError("commande `claude` introuvable: installe Claude Code ou prends backend=mock/api")

    def _call(self, system, user):
        cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--system-prompt", system]
        if self._model_flag:
            cmd += ["--model", self._model_flag]
        proc = self.runner(cmd, input=user, capture_output=True, text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise BackendError("claude -p a echoue: %s" % (proc.stderr or proc.stdout)[:400])
        try:
            wrapper = json.loads(proc.stdout)
            text = wrapper.get("result", "")
            meta = {"cost_usd": wrapper.get("total_cost_usd")}
        except (ValueError, AttributeError):
            text, meta = proc.stdout, {}
        return extract_json(text), meta

    def ask(self, agent, agent_prompt, payload):
        system = system_prompt(agent_prompt, payload["as_of"], payload["tickers"])
        return self._call(system, json.dumps(payload, ensure_ascii=False, indent=1))

    def revise(self, agent, prompt, mistakes):
        return self._call(REVISE_SYSTEM, revise_message(agent, prompt, mistakes))[0]


# ---------------------------------------------------------------- api

REVISE_SYSTEM = """Tu ameliores le prompt d'un agent de recherche boursiere (papier uniquement).
On te donne son prompt actuel et ses pires erreurs recentes (periode d'entrainement).
Propose UNE seule modification ciblee qui corrige le defaut le plus frequent.
Garde tout le reste du prompt identique. Ne promets aucun resultat.
Reponds par un seul objet JSON: {"rationale": "...", "new_prompt": "...le prompt complet modifie..."}"""

REVISE_SCHEMA = {
    "type": "object",
    "properties": {"rationale": {"type": "string"}, "new_prompt": {"type": "string"}},
    "required": ["rationale", "new_prompt"],
    "additionalProperties": False,
}


def revise_message(agent, prompt, mistakes):
    return json.dumps({"agent": agent, "prompt_actuel": prompt, "erreurs": mistakes},
                      ensure_ascii=False, indent=1)


class ApiBackend:
    """Anthropic API (paid). Key from ANTHROPIC_API_KEY, never from a file in Git."""
    name = "api"

    def __init__(self, model="claude-opus-5-5", effort="medium", client=None):
        self.model = model
        self.effort = effort
        if client is None:
            try:
                import anthropic
            except ImportError:
                raise BackendError("module anthropic absent: `pip install anthropic` (seulement pour backend=api)")
            client = anthropic.Anthropic()
        self.client = client

    def _call(self, system, user, schema):
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            raise BackendError("le modele a refuse la demande")
        if resp.stop_reason == "max_tokens":
            raise BackendError("reponse coupee (max_tokens)")
        text = next(b.text for b in resp.content if b.type == "text")
        usage = getattr(resp, "usage", None)
        meta = {"input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None)}
        return json.loads(text), meta

    def ask(self, agent, agent_prompt, payload):
        system = system_prompt(agent_prompt, payload["as_of"], payload["tickers"])
        return self._call(system, json.dumps(payload, ensure_ascii=False, indent=1), OUTPUT_SCHEMA)

    def revise(self, agent, prompt, mistakes):
        return self._call(REVISE_SYSTEM, revise_message(agent, prompt, mistakes), REVISE_SCHEMA)[0]


def make(cfg):
    kind = cfg.get("backend", "mock")
    if kind == "mock":
        return MockBackend()
    if kind == "claude-code":
        return ClaudeCodeBackend(model=cfg.get("claude_code_model"))
    if kind == "api":
        return ApiBackend(model=cfg.get("api_model", "claude-opus-5-5"), effort=cfg.get("api_effort", "medium"))
    raise BackendError("backend inconnu: %s (mock, claude-code ou api)" % kind)
