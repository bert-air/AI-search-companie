# Signal Keywords — Filtrage des posts LinkedIn

Les posts LinkedIn des dirigeants sont filtrés par mots-clés avant d'être transmis aux agents LLM.
Seuls les posts contenant au moins un mot-clé conservent leur texte complet (500 chars).
Les autres posts ne conservent que les métadonnées (auteur, date, réactions).

## Liste des mots-clés

Code source : `hat_yai/utils/agent_runner.py` → `_SIGNAL_KEYWORDS`

### Transformation / Digital
| Mot-clé | Signaux liés |
|---------|-------------|
| transformation, transfo | `programme_transfo_annonce`, `posts_linkedin_transfo` |
| digitale, digital | `programme_transfo_annonce`, `posts_linkedin_transfo` |
| innovation | `programme_transfo_annonce` |
| modernisation | `programme_transfo_annonce` |
| dématérialisation, numérique | `programme_transfo_annonce` |
| industrie 4.0 | `programme_transfo_annonce` |

### IT / DSI / Infrastructure
| Mot-clé | Signaux liés |
|---------|-------------|
| dsi, cio, cto | `nouveau_dsi_dir_transfo`, `dsi_plus_40`, `dsi_en_poste_plus_5_ans` |
| directeur des systèmes, directeur informatique | `nouveau_dsi_dir_transfo` |
| directeur technique | `nouveau_dsi_dir_transfo` |
| chief information, chief technology | `nouveau_dsi_dir_transfo` |
| erp, sap, salesforce | comex_organisation (stack technique) |
| cloud, aws, azure, gcp | comex_organisation (stack technique) |
| migration | `programme_transfo_annonce` |
| cybersécurité, cyber | comex_organisation (stack technique) |
| infra, infrastructure | comex_organisation |
| servicenow, jira, monday, planview, ms project | comex_organisation (outillage PPM/PM) |
| devops, saas, data | comex_organisation (stack technique) |
| ia, intelligence artificielle | comex_organisation (stack technique) |

### PMO / Gestion de projets
| Mot-clé | Signaux liés |
|---------|-------------|
| pmo | `pmo_identifie` |
| bureau de projets | `pmo_identifie` |
| project management, program manager, programme manager | `pmo_identifie` |
| portefeuille de projets, gestion de projets | `pmo_identifie` |
| chef de projet, directeur de programme | `pmo_identifie` |
| roadmap, feuille de route | `plan_strategique_annonce` |

### Recrutement / RH
| Mot-clé | Signaux liés |
|---------|-------------|
| recrute, recrutement | croissance, comex_organisation |
| hiring, rejoindre, rejoignez | croissance |
| cdi, embauche | croissance |
| talent, onboarding | croissance |

### Strategie / Plans
| Mot-clé | Signaux liés |
|---------|-------------|
| plan stratégique | `plan_strategique_annonce` |
| stratégie, vision, ambition | `plan_strategique_annonce` |
| plan directeur, schéma directeur | `plan_strategique_annonce` |
| cap, objectif stratégique | `plan_strategique_annonce` |

### M&A / Restructuration
| Mot-clé | Signaux liés |
|---------|-------------|
| acquisition, fusion, rachat, cession | `acquisition_recente` |
| m&a | `acquisition_recente` |
| pse, licenciement, plan social | `licenciements_pse` |
| restructuration, réorganisation | `licenciements_pse` |
| plan de sauvegarde | `licenciements_pse` |

### Finance / Croissance
| Mot-clé | Signaux liés |
|---------|-------------|
| chiffre d'affaires, croissance | `croissance_ca_forte` |
| résultats | `croissance_ca_forte` |
| levée de fonds, investissement | finance |
| budget it, budget informatique | `dsi_plus_40` |

## Comment ajouter un mot-clé

1. Ajouter le mot-clé dans `_SIGNAL_KEYWORDS` dans `hat_yai/utils/agent_runner.py`
2. Mettre à jour ce fichier
3. Les mots-clés sont en minuscules, la recherche est case-insensitive
4. Attention aux mots courts qui pourraient matcher trop largement (ex: "ia " avec espace pour éviter de matcher "social")
