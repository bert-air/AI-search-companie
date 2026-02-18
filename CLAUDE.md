# CLAUDE.md — Project conventions

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

### Sources de verite

- **LangSmith** = comportement agent (traces)
- **Supabase** = donnees (logs, tables)
