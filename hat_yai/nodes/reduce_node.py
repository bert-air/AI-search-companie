"""REDUCE node — consolidate MAP lot results into a single enriched JSON.

Merges all lots, deduplicates, builds org chart, pre-detects signals.

Spec reference: Section 4.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from langchain_core.messages import SystemMessage, HumanMessage

from hat_yai.models_mapreduce import ConsolidatedLinkedIn
from hat_yai.state import AuditState
from hat_yai.utils.llm import get_llm, get_fast_llm, load_prompt_template

logger = logging.getLogger(__name__)

_LOTS_THRESHOLD_FOR_OPUS = 4  # Use Opus if > this many lots

# Title keywords → role mapping for C-level fallback
_ROLE_KEYWORDS = [
    ("ceo", "CEO"), ("chief executive", "CEO"), ("directeur général", "CEO"),
    ("pdg", "CEO"), ("président", "CEO"),
    ("cfo", "CFO"), ("chief financial", "CFO"), ("directeur financier", "CFO"),
    ("cio", "CIO"), ("chief information", "CIO"), ("dsi", "CIO"),
    ("cto", "CTO"), ("chief technical", "CTO"), ("chief technology", "CTO"),
    ("cdo", "CDO"), ("chief digital", "CDO"), ("chief data", "CDO"),
    ("coo", "COO"), ("chief operating", "COO"),
    ("cmo", "CMO"), ("chief marketing", "CMO"),
    ("chro", "CHRO"), ("chief human", "CHRO"), ("drh", "CHRO"),
    ("vp it", "VP_IT"), ("vp digital", "VP_Digital"), ("vp sales", "VP_Sales"),
    ("vp transformation", "VP_Transfo"), ("vp operations", "VP_Operations"),
    ("svp", "VP_Operations"), ("senior vice president", "VP_Operations"),
]


def _infer_role(title: str) -> str:
    """Infer C-level role from job title. Best-effort, used as fallback."""
    title_lower = title.lower()
    for keyword, role in _ROLE_KEYWORDS:
        if keyword in title_lower:
            return role
    return "Autre"


def _empty_consolidated(company_name: str) -> dict:
    """Return a minimal consolidated result for degraded mode."""
    return ConsolidatedLinkedIn(
        company_name=company_name,
        extraction_date=date.today().isoformat(),
    ).model_dump()


async def reduce_node(state: AuditState) -> dict:
    """REDUCE: consolidate all MAP lot results into one JSON."""
    lot_results = state.get("map_lot_results") or []
    company_name = state["company_name"]

    if not lot_results:
        logger.info("REDUCE: No MAP results to consolidate")
        empty = _empty_consolidated(company_name)
        # Still inject growth data even without MAP lots
        growth = state.get("linkedin_employees_growth")
        if growth:
            empty["croissance_effectifs"] = growth
            logger.info("REDUCE: Injected growth data into empty consolidated")
        return {"consolidated_linkedin": empty}

    total_lots = len(lot_results)

    # Build prompt
    prompt = load_prompt_template(
        "reduce",
        company_name=company_name,
        total_lots=str(total_lots),
    )

    # Build context: all lot JSONs + growth data
    context_parts = [
        f"# Extractions LinkedIn — {total_lots} lots\n",
    ]
    for lot in lot_results:
        lot_num = lot.get("lot_number", "?")
        context_parts.append(f"## Lot {lot_num}")
        context_parts.append(f"```json\n{json.dumps(lot, ensure_ascii=False, indent=2)}\n```\n")

    # Inject employee growth data (not part of MAP, comes from GG directly)
    growth = state.get("linkedin_employees_growth")
    if growth:
        context_parts.append("## Données de croissance effectifs (source LinkedIn)")
        context_parts.append(f"```json\n{json.dumps(growth, ensure_ascii=False, indent=2)}\n```\n")
        context_parts.append(
            "Intègre ces données dans le champ `croissance_effectifs` du JSON consolidé."
        )

    context = "\n".join(context_parts)

    # Model selection: Sonnet for <=4 lots, Opus for >4
    if total_lots > _LOTS_THRESHOLD_FOR_OPUS:
        logger.info(f"REDUCE: {total_lots} lots > {_LOTS_THRESHOLD_FOR_OPUS}, using Opus")
        llm = get_llm(max_tokens=16384)
    else:
        llm = get_fast_llm(max_tokens=16384)

    structured_llm = llm.with_structured_output(ConsolidatedLinkedIn)

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=context),
    ]

    try:
        result = await structured_llm.ainvoke(messages)
    except Exception as e:
        logger.error(f"REDUCE: Structured output failed: {e}, retrying with Opus")
        llm = get_llm(max_tokens=8192)
        structured_llm = llm.with_structured_output(ConsolidatedLinkedIn)
        result = await structured_llm.ainvoke(messages)

    consolidated = result.model_dump()

    # --- Post-LLM: merge large lists in pure Python ---
    # The LLM's max_tokens budget (8192) is too small to output 50+ profiles,
    # 100+ posts, etc. We merge these from MAP lot results directly and let
    # the LLM focus on the "intelligence" outputs (c_levels, organigramme,
    # themes, signaux) which are small enough to fit.

    # 1. Dirigeants: merge + deduplicate by name
    all_dirigeants: list[dict] = []
    seen_names: set[str] = set()
    for lot in lot_results:
        for d in lot.get("dirigeants") or []:
            name = d.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                all_dirigeants.append(d)
            elif name in seen_names:
                # Keep the more complete version (more non-null fields)
                for i, existing in enumerate(all_dirigeants):
                    if existing.get("name") == name:
                        existing_fields = sum(1 for v in existing.values() if v)
                        new_fields = sum(1 for v in d.values() if v)
                        if new_fields > existing_fields:
                            all_dirigeants[i] = d
                        break
    # 1bis. Filter out non-employees (profiles from other companies)
    company_lower = company_name.lower()
    company_variants = {company_lower, company_lower.replace(" ", ""),
                        company_lower.replace("-", " "), company_lower.replace("-", "")}
    pre_filter_count = len(all_dirigeants)
    filtered_dirigeants = []
    for d in all_dirigeants:
        d_company = (d.get("company_name") or "").lower()
        # Keep if: no company data (benefit of doubt) or company matches target
        if not d_company or any(v in d_company for v in company_variants):
            filtered_dirigeants.append(d)
        else:
            logger.info(
                f"REDUCE: Filtered out {d.get('name')} — "
                f"company '{d.get('company_name')}' ≠ {company_name}"
            )
    all_dirigeants = filtered_dirigeants
    if pre_filter_count != len(all_dirigeants):
        logger.info(
            f"REDUCE: Filtered {pre_filter_count - len(all_dirigeants)} "
            f"non-employee profiles ({pre_filter_count} → {len(all_dirigeants)})"
        )
    consolidated["dirigeants"] = all_dirigeants

    # 2. Posts: merge + deduplicate
    all_posts: list[dict] = []
    seen_posts: set[str] = set()
    for lot in lot_results:
        for post in lot.get("posts_pertinents") or []:
            key = (
                post.get("auteur", ""),
                post.get("date", ""),
                (post.get("texte_integral") or "")[:100],
            )
            key_str = str(key)
            if key_str not in seen_posts:
                seen_posts.add(key_str)
                all_posts.append(post)
    consolidated["posts_pertinents"] = all_posts

    # 3. Mouvements: merge from mouvements_lot + deduplicate
    all_mouvements: list[dict] = []
    seen_mouvements: set[str] = set()
    for lot in lot_results:
        for m in lot.get("mouvements_lot") or []:
            key = (m.get("qui", ""), m.get("type", ""), m.get("date_approx", ""))
            key_str = str(key)
            if key_str not in seen_mouvements:
                seen_mouvements.add(key_str)
                all_mouvements.append(m)
    # Sort by date desc
    all_mouvements.sort(key=lambda m: m.get("date_approx", ""), reverse=True)
    consolidated["mouvements_consolides"] = all_mouvements

    # 4. Stack: merge from stack_detectee_lot + deduplicate
    all_stack: list[dict] = []
    seen_tools: set[str] = set()
    for lot in lot_results:
        for tool_name in lot.get("stack_detectee_lot") or []:
            if isinstance(tool_name, str) and tool_name not in seen_tools:
                seen_tools.add(tool_name)
                all_stack.append({"outil": tool_name, "source": "lot", "mentionne_par": ""})
    # Merge with any LLM-produced stack entries (which may have richer source info)
    for entry in consolidated.get("stack_consolidee") or []:
        tool = entry.get("outil", "")
        if tool and tool not in seen_tools:
            seen_tools.add(tool)
            all_stack.append(entry)
    consolidated["stack_consolidee"] = all_stack

    # 5. C-levels: fallback from dirigeants if LLM didn't produce them
    llm_c_levels = consolidated.get("c_levels") or []
    if not llm_c_levels:
        # Build c_levels from dirigeants marked is_c_level by MAP
        c_level_dirigeants = [d for d in all_dirigeants if d.get("is_c_level")]
        consolidated["c_levels"] = [
            {
                "name": d.get("name", ""),
                "current_title": d.get("current_title", ""),
                "anciennete_mois": d.get("anciennete_mois"),
                "role_deduit": _infer_role(d.get("current_title", "")),
                "pertinence_commerciale": 3,  # default mid-range, agents will refine
            }
            for d in c_level_dirigeants
        ]
        logger.info(
            f"REDUCE: Built {len(consolidated['c_levels'])} C-levels from "
            f"dirigeants (LLM produced 0)"
        )

    # 6. PMO detection fallback: scan about, skills, title for PMO IT signals
    llm_signals = consolidated.get("signaux_pre_detectes") or []
    pmo_detected_by_llm = any(
        s.get("signal_id") == "pmo_identifie" and s.get("probable")
        for s in llm_signals
    )
    if not pmo_detected_by_llm:
        _PMO_KEYWORDS = {"pmo", "project management office", "bureau de projets",
                         "project portfolio management", "it portfolio management",
                         "portefeuille projets"}
        _IT_CONTEXT = {"it", "si", "dsi", "cio", "digital", "informatique",
                       "systems", "systèmes", "information"}
        for d in all_dirigeants:
            # Collect all text fields to scan
            title = (d.get("current_title") or "").lower()
            about = (d.get("about") or "").lower()
            skills = [s.lower() for s in (d.get("skills_cles") or [])]
            headline = " ".join(d.get("headline_keywords") or []).lower()
            all_text = f"{title} {about} {headline} {' '.join(skills)}"
            # Check for PMO keyword match
            pmo_match = any(kw in all_text for kw in _PMO_KEYWORDS)
            if pmo_match:
                # Validate IT context: about/title/skills mention IT-related terms
                has_it_context = any(ctx in all_text for ctx in _IT_CONTEXT)
                rattachement = (d.get("rattachement_mentionne") or "").lower()
                if rattachement:
                    has_it_context = has_it_context or any(
                        ctx in rattachement for ctx in _IT_CONTEXT
                    )
                if has_it_context:
                    llm_signals.append({
                        "signal_id": "pmo_identifie",
                        "probable": True,
                        "evidence": f"PMO IT détecté via profil (about/skills/titre)",
                        "source": d.get("name", ""),
                    })
                    consolidated["signaux_pre_detectes"] = llm_signals
                    logger.info(
                        f"REDUCE: PMO IT detected (Python-fallback) from "
                        f"{d.get('name')}"
                    )
                    break

    # 6b. nouveau_dsi_dir_transfo fallback: scan for recent IT/Digital leader
    dsi_detected_by_llm = any(
        s.get("signal_id") == "nouveau_dsi_dir_transfo" and s.get("probable")
        for s in llm_signals
    )
    if not dsi_detected_by_llm:
        _DSI_KEYWORDS = {
            "dsi", "cio", "cto", "cdo", "chief information",
            "chief technology", "chief digital", "chief data",
            "directeur des systèmes", "directeur digital",
            "directeur de la transformation", "dir transfo",
            "vp it", "svp it", "group digital", "group it",
        }
        for d in all_dirigeants:
            if (d.get("is_c_level")
                    and d.get("is_current_employee", True)
                    and (d.get("anciennete_mois") or 999) < 12):
                title_lower = (d.get("current_title") or "").lower()
                if any(kw in title_lower for kw in _DSI_KEYWORDS):
                    llm_signals.append({
                        "signal_id": "nouveau_dsi_dir_transfo",
                        "probable": True,
                        "evidence": (
                            f"{d.get('name')} — {d.get('current_title')} "
                            f"(ancienneté {d.get('anciennete_mois')} mois)"
                        ),
                        "source": d.get("name", ""),
                    })
                    consolidated["signaux_pre_detectes"] = llm_signals
                    logger.info(
                        f"REDUCE: nouveau_dsi_dir_transfo detected "
                        f"(Python-fallback) from {d.get('name')} "
                        f"({d.get('anciennete_mois')} months)"
                    )
                    break

    today = date.today()

    # 6c. direction_transfo_existe fallback: transformation/digital director
    direction_transfo_by_llm = any(
        s.get("signal_id") == "direction_transfo_existe" and s.get("probable")
        for s in llm_signals
    )
    if not direction_transfo_by_llm:
        _TRANSFO_TITLE_KW = {
            "transformation", "digital", "cdo", "chief digital",
            "chief data", "directeur digital", "directeur de la transformation",
            "dir transfo", "numérique",
        }
        for d in all_dirigeants:
            if d.get("is_c_level") and d.get("is_current_employee", True):
                title_lower = (d.get("current_title") or "").lower()
                if any(kw in title_lower for kw in _TRANSFO_TITLE_KW):
                    llm_signals.append({
                        "signal_id": "direction_transfo_existe",
                        "probable": True,
                        "evidence": (
                            f"{d.get('name')} — {d.get('current_title')}"
                        ),
                        "source": d.get("name", ""),
                    })
                    consolidated["signaux_pre_detectes"] = llm_signals
                    logger.info(
                        f"REDUCE: direction_transfo_existe detected "
                        f"(Python-fallback) from {d.get('name')}"
                    )
                    break

    # 6d. posts_linkedin_transfo fallback: ≥2 C-level posts with transfo topic
    posts_transfo_by_llm = any(
        s.get("signal_id") == "posts_linkedin_transfo" and s.get("probable")
        for s in llm_signals
    )
    if not posts_transfo_by_llm:
        # Build set of C-level names (lowercase) for author matching
        c_level_names_lower = set()
        for cl in consolidated.get("c_levels") or []:
            c_level_names_lower.add(cl.get("name", "").lower())
        for d in all_dirigeants:
            if d.get("is_c_level"):
                c_level_names_lower.add(d.get("name", "").lower())

        # Date cutoff: 6 months ago
        _m = today.month - 6
        _y = today.year
        while _m <= 0:
            _m += 12
            _y -= 1
        cutoff_6m = f"{_y}-{_m:02d}-01"

        transfo_posts = []
        for post in all_posts:
            auteur_lower = (post.get("auteur") or "").lower()
            post_date = post.get("date") or ""
            topics = post.get("topics") or []
            if (auteur_lower in c_level_names_lower
                    and post_date >= cutoff_6m
                    and "transformation_digitale" in topics):
                transfo_posts.append(post)

        if len(transfo_posts) >= 2:
            auteurs = list({p.get("auteur", "") for p in transfo_posts})
            llm_signals.append({
                "signal_id": "posts_linkedin_transfo",
                "probable": True,
                "evidence": (
                    f"{len(transfo_posts)} posts transfo par C-levels: "
                    f"{', '.join(auteurs[:3])}"
                ),
                "source": ", ".join(auteurs[:3]),
            })
            consolidated["signaux_pre_detectes"] = llm_signals
            logger.info(
                f"REDUCE: posts_linkedin_transfo detected "
                f"(Python-fallback) — {len(transfo_posts)} posts from "
                f"{', '.join(auteurs[:3])}"
            )

    # 7. Turnover COMEX: detect ≥3 C-level departures in 18 months
    cutoff_month = today.month - 18
    cutoff_year = today.year
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    cutoff_str = f"{cutoff_year}-{cutoff_month:02d}"

    c_level_names = {
        d.get("name", "").lower()
        for d in all_dirigeants if d.get("is_c_level")
    }
    recent_c_departures = [
        m for m in all_mouvements
        if m.get("type") in ("depart", "départ")
        and m.get("date_approx", "") >= cutoff_str
        and m.get("qui", "").lower() in c_level_names
    ]
    if len(recent_c_departures) >= 3:
        names = [m.get("qui", "") for m in recent_c_departures]
        llm_signals.append({
            "signal_id": "turnover_comex_detecte",
            "probable": True,
            "evidence": (
                f"{len(recent_c_departures)} départs C-level en 18 mois: "
                f"{', '.join(names[:5])}"
            ),
            "source": "mouvements_consolides",
        })
        consolidated["signaux_pre_detectes"] = llm_signals
        logger.info(
            f"REDUCE: COMEX turnover detected — "
            f"{len(recent_c_departures)} C-level departures in 18 months"
        )

    # 8. Role evolution: new C-levels with digital/transfo titles + recent departures
    _DIGITAL_SHIFT_KEYWORDS = {
        "digital", "transformation", "data", "cdo", "chief digital",
        "innovation", "numérique",
    }
    new_digital_c_levels = []
    for d in all_dirigeants:
        if (d.get("is_c_level")
                and d.get("is_current_employee", True)
                and (d.get("anciennete_mois") or 999) < 18):
            title_lower = (d.get("current_title") or "").lower()
            if any(kw in title_lower for kw in _DIGITAL_SHIFT_KEYWORDS):
                new_digital_c_levels.append(d.get("name", ""))

    if new_digital_c_levels and recent_c_departures:
        llm_signals.append({
            "signal_id": "evolution_roles_comex",
            "probable": True,
            "evidence": (
                f"Nouveaux C-levels digital/transfo: "
                f"{', '.join(new_digital_c_levels[:3])} + "
                f"{len(recent_c_departures)} départs récents"
            ),
            "source": "dirigeants + mouvements",
        })
        consolidated["signaux_pre_detectes"] = llm_signals
        logger.info(
            f"REDUCE: Role evolution detected — "
            f"new digital C-levels: {new_digital_c_levels}"
        )

    # Update metadata counts from actual merged data
    consolidated["profils_total"] = len(all_dirigeants)
    consolidated["profils_c_level"] = len(consolidated.get("c_levels") or [])

    # Ensure growth data is included even if LLM missed it
    if growth and not consolidated.get("croissance_effectifs"):
        consolidated["croissance_effectifs"] = growth

    # Ensure metadata
    consolidated["extraction_date"] = consolidated.get("extraction_date") or date.today().isoformat()
    consolidated["lots_fusionnes"] = total_lots

    logger.info(
        f"REDUCE: Consolidated {len(all_dirigeants)} profiles (Python-merged), "
        f"{consolidated.get('profils_c_level', 0)} C-levels "
        f"({'Python-fallback' if not llm_c_levels else 'LLM'}), "
        f"{len(all_posts)} posts, {len(all_mouvements)} mouvements, "
        f"{len(all_stack)} stack entries, "
        f"{len(consolidated.get('signaux_pre_detectes', []))} pre-signals"
    )

    return {"consolidated_linkedin": consolidated}
