# CLAUDE.md — Project conventions

## Core Principles

- **Simplicite d'abord** — chaque changement doit etre aussi simple que possible, impacter un minimum de code
- **Pas de laziness** — toujours chercher la cause racine, jamais de fix temporaire. Standards de senior developer
- **Impact minimal** — ne toucher que ce qui est necessaire. Eviter d'introduire des bugs colateraux
- **Elegance (equilibree)** — pour les changements non-triviaux, se demander "y a-t-il une solution plus elegante ?". Ne pas sur-ingenierer les fix simples

## Git workflow

- **Default branch for commits/push: `staging`**
- Never push directly to `main`
- When the user says "commit et push" or similar, always commit and push to `staging`
- Merge to `main` only on explicit instruction ("merge sur main") — create a PR via `gh pr create` from `staging` → `main`
- Run E2E tests from `staging` branch

## LangSmith / LangGraph Platform

- **Region EU** — toutes les API sont sur `eu.`:
  - LangSmith API: `https://eu.api.smith.langchain.com`
  - LangGraph Control Plane: `https://eu.api.host.langchain.com`
  - Dashboard: `https://eu.smith.langchain.com`
- Ne JAMAIS utiliser `api.smith.langchain.com` (US) — retourne 403
- Org: **Airsaas**, Workspace: **Internal Tool**
- API Key header: `X-API-Key` + `X-Tenant-Id` pour le workspace

## Local development

- **Toujours tester en local d'abord** avec `langgraph dev` avant tout deploiement cloud
- Le deploiement cloud se fait uniquement quand l'utilisateur le demande explicitement
- Config locale: `langgraph.json` + `.env`
- Venv Python 3.11 requis (`.venv/`), le Python 3.14 systeme casse des wheels
- Commande: `source .venv/bin/activate && langgraph dev --no-browser --port 8123 --allow-blocking`
- `--allow-blocking` necessaire car supabase_db fait des appels sync dans des nodes async
- API locale: `http://localhost:8123` — docs: `http://localhost:8123/docs`

## Gestion des secrets

- Les cles API sont stockees dans **LangSmith Workspace Secrets** (backup cloud)
  - Endpoint: `POST/GET https://eu.api.smith.langchain.com/api/v1/workspaces/current/secrets`
  - Headers: `X-API-Key` + `X-Tenant-Id: dcf94fc8-6f68-4630-b909-34eaef6b155e`
- Si une cle dans `.env` est un placeholder (`...`, `gg_...`, `pat-...`, `lsv2-...`):
  1. Chercher d'abord dans LangSmith secrets
  2. Demander a l'utilisateur en dernier recours
- `.env` est dans `.gitignore` — ne JAMAIS committer de vraies cles
- `.env.example` contient les placeholders pour reference
- GHOST_GENIUS_ACCOUNT_IDS provient de Supabase `workspace_team.ghost_genius_account_id` (status=active)

## Architecture diagram (auto-update)

- Le fichier `docs/agent-flow.md` contient le diagramme Mermaid detaille du flux complet des agents
- **A chaque modification du graph, des nodes, ou des cascades** (fichiers `graph.py`, `*_node.py`, `tools/*.py`), mettre a jour `docs/agent-flow.md` pour refleter le changement
- Le diagramme couvre : graph high-level, cascade Step 1, pipeline LinkedIn 5 etapes, cascade executive search, scoring/verdicts
- Objectif : toujours avoir une vue a jour du systeme, lisible sans lire le code

## Plan Mode & Execution Strategy

### Quand entrer en plan mode

- **Obligatoire** pour toute tache non-triviale (3+ etapes ou decisions architecturales)
- Si quelque chose deraille en cours de route → **STOP et re-planifier immediatement**, ne pas insister
- Utiliser le plan mode aussi pour les etapes de verification, pas seulement le build
- Ecrire des specs detaillees en amont pour reduire l'ambiguite

### Exigence d'elegance

- Pour les changements non-triviaux : pause et demander "y a-t-il une facon plus elegante ?"
- Si un fix semble hacky : "Sachant tout ce que je sais maintenant, implementer la solution elegante"
- Pour les fix simples et evidents : skip, ne pas sur-ingenierer

## Subagent Strategy

- Utiliser les subagents **liberalement** pour garder la fenetre de contexte principale propre
- Deleguer recherche, exploration et analyses paralleles aux subagents
- Pour les problemes complexes : jeter plus de compute via subagents
- **Une tache par subagent** pour une execution focalisee

## Task Management & Self-Improvement

### Gestion des taches

- **Plan d'abord** : ecrire le plan avec des items checkables avant d'implementer
- **Verifier le plan** : valider avec l'utilisateur avant de commencer
- **Suivre la progression** : marquer les items complete au fur et a mesure
- **Expliquer les changements** : resume haut-niveau a chaque etape

### Boucle d'auto-amelioration

- Apres **toute correction** de l'utilisateur : noter le pattern dans la memoire projet
- Ecrire des regles pour soi-meme qui empechent la meme erreur
- Iterer sans pitie sur ces lecons jusqu'a ce que le taux d'erreur baisse
- Relire les lecons au debut de chaque session pour le projet concerne

## Autonomous Bug Fixing

- Quand un bug est signale : **le fixer directement**, ne pas demander de l'aide pas-a-pas
- Pointer les logs, erreurs, tests qui echouent — puis les resoudre
- Zero context-switching requis de la part de l'utilisateur
- Aller fixer les tests CI qui echouent sans qu'on explique comment

## Agent Development Harness

Philosophie : Harness Engineering — preparer le contexte, lancer les tests, lire les traces, iterer.

### Boucle obligatoire a chaque test

1. **PLAN** — Identifier le critere de succes mesurable avant d'ecrire du code
2. **BUILD** — Implementer avec la verification en tete
3. **RUN & TRACE** — Apres chaque run :
   - Lire les traces LangSmith (inputs → outputs → erreurs)
   - Lire les logs Supabase (30 dernieres minutes, tri desc)
4. **EVALUE** — L'agent a-t-il compris la tache ? Output verifie ? Doom loop ?
5. **ITERE** — Si critere non atteint : identifier cause racine, changer une seule chose, relancer. Max 5 iterations.

### Regles anti-doom-loop

- Si meme fichier edite > 3 fois sans amelioration mesurable → stop, reconsiderer l'approche
- Si meme erreur 2 runs de suite → le probleme est dans le harness, pas l'implementation
- Ne jamais valider sur "ca a l'air bon dans le code" — valider uniquement sur traces + logs

### Checklist pre-cloture (obligatoire)

- [ ] Le test passe effectivement
- [ ] La trace LangSmith montre les etapes attendues sans erreur
- [ ] Les logs Supabase ne contiennent pas d'anomalie
- [ ] Le comportement tient sur un cas limite
- [ ] **"Un staff engineer approuverait-il ce code ?"** — ne jamais marquer une tache complete sans prouver qu'elle fonctionne

### Sources de verite

- **LangSmith** = comportement agent (traces)
- **Supabase** = donnees (logs, tables)

### Checklist post-audit (obligatoire apres chaque run)

Apres chaque audit termine, analyser systematiquement **les traces LangSmith ET les donnees Supabase**, puis poster le resume dans le chat.

#### A. Traces LangSmith (comportement agents)

Ouvrir la trace du run dans le dashboard EU (`https://eu.smith.langchain.com`) :

1. **Duree totale** — comparer avec les runs precedents, identifier les etapes lentes
2. **Parcours du graph** — verifier que tous les noeuds attendus ont ete executes (orchestrator → linkedin_enrichment → MAP agents → reduce → scoring → synthesizer)
3. **Agents MAP** — pour chaque agent (entreprise, finance, dynamique, comex_org, comex_profils, connexions) :
   - Nombre de tool calls (search_web, scrape_page) vs limites du prompt
   - Erreurs ou retries
   - Taille du contexte injecte (tokens)
   - Qualite du structured output (AgentReport parse correctement ?)
4. **Enrichissement LinkedIn** — Steps 3a/3b/4/5 : quelle API a repondu (Evaboot/Unipile/GG), combien de resultats, timeouts
5. **Scoring** — verifier que les signaux des agents arrivent bien au scoring node
6. **Synthesizer** — slack_recap genere ? Taille ? Erreur de parsing ?

#### B. Donnees Supabase (resultats persistes)

Requeter `ai_agent_company_audit_reports` + `_executives` + `_linkedin_posts` :

1. **Metadata** — score, verdict, duree, data_quality_score
2. **Executives** — total, current/past split, enrichment_status breakdown (cached/enriched/failed), connected_with coverage (% avec donnees)
3. **Posts** — total, auteurs uniques, distribution par auteur (cap atteint ?), date range couvert
4. **Agent reports** — pour chaque agent : data_quality (sources_count, confidence_overall, linkedin_available), nombre de facts, nombre de signals
5. **Scoring signals** — DETECTED/NOT_DETECTED/UNKNOWN counts, points bruts vs ponderes, coherence avec les reports agents

#### C. Anomalies a flagger (priorite haute → basse)

- Enrichments failed sur des profils C-level (CTO, DSI, CDO)
- Agent avec 0 facts ou 0 sources (= n'a rien trouve)
- Signal avec signal_id malformeou inconnu du SCORING_GRILLE
- Template variables non resolues dans evidence (`{{...}}`)
- connected_with = 0 → agent Connexions aveugle (4 UNKNOWN)
- confidence "low" sur un agent critique (comex_org, comex_profils)
- Slack recap vide ou tronque
- Duree > 30 min (identifier le bottleneck)
- Posts cap atteint sur tous les auteurs (= on manque potentiellement du contenu)
