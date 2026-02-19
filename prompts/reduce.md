# Consolidation LinkedIn — {{company_name}}

Tu reçois les extractions structurées de {{total_lots}} lots de dirigeants de {{company_name}}. Ton rôle : consolider, dédupliquer, croiser, et pré-détecter des signaux.

## 1. dirigeants
Fusionner les listes de tous les lots. Dédupliquer par nom. Conserver tous les champs. En cas de doublon avec données conflictuelles, garder la version la plus complète.

## 2. c_levels
Extraire les dirigeants avec is_c_level = true. Pour chacun, ajouter :
- "role_deduit" : classifier en CEO|CFO|CIO|CTO|CDO|COO|CMO|CHRO|VP_IT|VP_Digital|VP_Sales|VP_Transfo|VP_Operations|BU_Head|Autre
- "pertinence_commerciale" : 1 à 5
  - 5 = décideur ou influenceur direct IT/digital/transformation/achats
  - 4 = C-level avec impact indirect (CEO, CFO)
  - 3 = VP/Director avec périmètre potentiellement pertinent
  - 2 = BU Head régional sans signal particulier
  - 1 = non pertinent pour notre offre

## 3. organigramme_probable
Croise les rattachements mentionnés dans les profils, les personnes citées dans les posts, et les titres pour reconstituer les liens hiérarchiques.
Format : [{"de": "", "vers": "", "relation": "reporte_a|meme_comex|mentionne_comme_equipe|supervise", "confidence": "high|medium|low"}]

Règles :
- "reporte_a" + confidence "high" = mentionné explicitement dans le profil ou un post
- "reporte_a" + confidence "medium" = déduit des titres (ex: DSI reporte probablement au CFO ou CEO)
- "meme_comex" = les deux ont des titres C-level et sont mentionnés ensemble

## 4. posts_pertinents
Fusionner tous les lots. Garder texte_integral. Trier par pertinence décroissante (posts avec verbatim_cle en premier, puis par date desc).

## 5. themes_transversaux
Identifier les sujets qui reviennent chez ≥2 dirigeants différents.
Format : [{"theme": "", "count": N, "auteurs": ["Prénom Nom", ...]}]

## 6. stack_consolidee
Fusionner les stack_detectee_lot de tous les lots, dédupliquer.
Format : [{"outil": "", "source": "post|profil|headline|offre", "mentionne_par": "Prénom Nom"}]

## 7. mouvements_consolides
Fusionner les mouvements de tous les lots, ordonner par date desc.

## 8. signaux_pre_detectes
En te basant UNIQUEMENT sur les données LinkedIn consolidées, pré-évalue ces signaux. Ce sont des HYPOTHÈSES — les agents en aval les confirmeront ou infirmeront.

| signal_id | Règle de détection |
|---|---|
| nouveau_pdg_dg | Un dirigeant CEO/DG/PDG avec anciennete_mois < 12 |
| nouveau_dsi_dir_transfo | Un dirigeant DSI/CIO/CTO/CDO/Dir Transfo/Chief Digital avec anciennete_mois < 12 |
| posts_linkedin_transfo | ≥2 posts de dirigeants C-level avec topic transformation_digitale dans les 6 derniers mois |
| direction_transfo_existe | Un dirigeant avec titre contenant "Transformation" ou "Digital" (hors DSI pur) identifié |
| pmo_identifie | Un PMO IT/Digital identifié. Chercher dans TOUS les champs du profil : current_title, about, headline_keywords, skills_cles. Mots-clés : "PMO", "Bureau de projets", "Project Management Office", "IT Portfolio Management", "Project Portfolio Management". VALIDATION : vérifier que le contexte indique un PMO IT (gestion de portefeuille de projets IT/SI, rattachement DSI/CIO) et non un chef de projet isolé ou un PMO métier (construction, etc.) |
| dsi_en_poste_plus_5_ans | Le DSI/CIO principal a anciennete_mois > 60 |
| verbatim_douleur_detecte | ≥1 post avec verbatim_cle non-null qui exprime un besoin/défi opérationnel |
| dirigeant_actif_linkedin | La personne avec pertinence_commerciale la plus élevée a ≥4 posts dans les données |

Format : [{"signal_id": "", "probable": bool, "evidence": "max 30 mots", "source": "nom de la personne ou du post"}]

## Output

Produis UN JSON consolidé avec les 8 sections ci-dessus + les métadonnées :
{
  "company_name": "",
  "extraction_date": "YYYY-MM-DD",
  "profils_total": N,
  "profils_c_level": N,
  "lots_fusionnes": N,
  ...les 8 sections...
}
