# CLAUDE.md — Project conventions

## Git workflow

- **Default branch for commits/push: `staging`**
- Never push directly to `main`
- When the user says "commit et push" or similar, always commit and push to `staging`
- Merge to `main` only on explicit instruction ("merge sur main") — create a PR via `gh pr create` from `staging` → `main`
- Run E2E tests from `staging` branch

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
