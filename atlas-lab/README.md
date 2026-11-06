# ATLAS-lab

Une petite equipe d'agents IA qui fait de la recherche boursiere, note ses
propres predictions et essaie d'ameliorer ses prompts. Inspire du design
public d'ATLAS (github.com/pgarvie/ATLAS), mais construit ici de zero: les
prompts entraines d'ATLAS ne sont pas publics.

**Papier uniquement.** Aucun ordre, aucun courtier, aucun argent reel. Le
dossier ne contient aucune adresse d'envoi d'ordre (un test le verifie).
Une performance simulee ne promet aucun gain. Pas un conseil financier.

## Demarrer en 10 secondes (gratuit)

```bash
cd atlas-lab
python3 lab.py demo
open out/dashboard.html        # sur Mac
```

La demo utilise des **donnees fictives** et des **agents fictifs** (des
regles fixes, pas une IA). Elle sert a voir la machine tourner. Le tableau
de bord l'affiche en rouge.

## Avec de vraies donnees et de vrais agents

1. Copie `config.example.json` en `config.json` et choisis tes 5 actions.
2. Donnees Alpaca (lecture seule, flux IEX gratuit). Les cles viennent de
   l'environnement, jamais d'un fichier dans Git:
   ```bash
   export ALPACA_API_KEY=...  ALPACA_SECRET_KEY=...
   python3 lab.py fetch
   ```
3. Choisis qui fait parler les agents, dans `config.json` (`"backend"`) ou
   avec `--backend`:
   - `mock`: regles fixes, gratuit, pour tester.
   - `claude-code`: lance `claude -p` sur ton Mac, avec ton compte Claude
     Code. Pas de cle API, pas de facture en plus (dans les limites de ton
     abonnement). Les outils sont coupes: l'agent ne lit que ce qu'on lui donne.
   - `api`: API Anthropic (payant). `pip install anthropic`, puis
     `export ANTHROPIC_API_KEY=...`. Modele par defaut `claude-opus-5-5`;
     mets `"api_model": "claude-sonnet-5-5"` pour diviser le cout par deux.
4. Une seance:
   ```bash
   python3 lab.py --backend claude-code debate          # dernier jour des donnees
   python3 lab.py report                                # score, simulation, tableau de bord
   ```

## L'equipe (etape 1)

| Couche | Agent | Role |
|---|---|---|
| 1 | macro | regime risk on / risk off a partir de l'indice |
| 2 | secteurs | force relative de chaque action contre l'indice |
| 3 | momentum, qualite | deux styles d'investisseur qui se contredisent |
| 4 | risque | avocat du diable, cherche ce qui peut casser |
| 5 | cio | synthese, liste classee finale |

Chaque agent est un fichier texte dans `agents/`. Chaque agent voit les
faits du jour et les avis des couches precedentes, rien d'autre. Pour passer
aux 25 roles d'ATLAS, on ajoute des fichiers et on les liste dans `layers`.

## Ce qui est enregistre (journal d'audit)

`runs/<date>/debate.json`, pour chaque seance: date de coupure des donnees,
source et heure de telechargement, empreinte des donnees et de chaque prompt,
backend et modele, commit du code, puis pour chaque agent ses avis (sens,
conviction, horizon, argument, scenario, sources citees).

## Comment c'est note

Une prediction faite a la cloture du jour J avec un horizon h est comparee au
rendement de J a J+h **moins l'indice** (SPY). Score = sens x ecart x
conviction. NEUTRAL n'est pas note. Le Sharpe n'est affiche qu'a partir de 30
predictions notees; en dessous, le tableau dit "echantillon trop petit".

Les dates sont coupees en trois: **entrainement**, **validation**, **test**
(dans `splits`), puis **live** apres. `backtest` saute la periode de test tant
qu'on ne passe pas `--include-test` (a faire une seule fois, a la fin).

## Le simulateur papier

Decision apres la cloture, execution a l'ouverture du lendemain, glissement
de prix (`slippage_bps`), frais (`fee_bps`, minimum `min_fee_usd`), achat
seulement, plafond par action (`max_position_pct`), reserve de cash
(`cash_buffer_pct`). Suivi de la baisse maximale, des frais, du volume
echange, et comparaison avec l'achat-conservation de l'indice.

## L'auto-amelioration

```bash
python3 lab.py experiment propose                 # agent le plus faible en entrainement
python3 lab.py experiment evaluate --agent macro  # rejoue sur la validation, GARDER ou ANNULER
```

Une seule modification a la fois. Le candidat est teste sur la periode de
validation, jamais sur le test. GARDER seulement si le gain est credible
(intervalle a 95 % au-dessus de zero, au moins 30 paires). La decision est
ecrite dans `experiments/log.jsonl` et commitee dans Git.

## Limites a garder en tete

- **Un backtest avec une IA est biaise.** Claude connait deja l'histoire
  jusqu'a sa date d'entrainement. Meme si on ne lui montre que les prix du
  passe, il peut "se souvenir" de la suite. Le backtest sert a verifier la
  machine. La vraie mesure, c'est le mode quotidien (`daily`) sur des jours
  qui n'ont pas encore eu lieu.
- Rejouer plusieurs experiences sur la meme validation finit par l'user.
  Garde le test intact jusqu'au bout.
- 5 jours ou 2 semaines, c'est trop court pour prouver un avantage.

## Le mode quotidien

Chaque soir apres la cloture americaine (22h30 a Paris):

```bash
python3 lab.py --backend claude-code daily
```

Telecharge les prix, fait debattre l'equipe, met a jour le score, la
simulation et le tableau de bord.

## Tests

```bash
python3 -m unittest discover -s atlas-lab/tests
```

Ils tournent sans reseau et sans cle: donnees fictives et faux backends.
