"""Pydantic models for the MAP/REDUCE pre-processing pipeline.

MAP: extracts structured data from raw LinkedIn profiles + posts (per lot).
REDUCE: consolidates all lots into a single enriched JSON.

These models are used with `with_structured_output()` for validated LLM output.
"""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


# --- MAP models (per-lot extraction) ---


class EntreprisePrecedente(BaseModel):
    nom: str
    poste: str
    duree_mois: Optional[int] = None


class MapDirigeant(BaseModel):
    name: str
    current_title: str
    poste_debut: Optional[str] = None  # "YYYY-MM"
    anciennete_mois: Optional[int] = None
    is_c_level: bool = False
    is_current_employee: bool = True
    entreprises_precedentes: list[EntreprisePrecedente] = Field(default_factory=list)
    headline_keywords: list[str] = Field(default_factory=list)
    rattachement_mentionne: Optional[str] = None
    personnes_mentionnees: list[str] = Field(default_factory=list)
    skills_cles: list[str] = Field(default_factory=list)
    connected_with: Optional[list[str]] = None
    about: Optional[str] = None  # LinkedIn "About" section (when available)
    company_name: Optional[str] = None  # Current employer (for filtering)


class MapPost(BaseModel):
    auteur: str
    auteur_titre: str = ""
    date: str = ""  # "YYYY-MM-DD"
    texte_integral: str = ""
    outils_mentionnes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    verbatim_cle: Optional[str] = None


class MapMouvement(BaseModel):
    qui: str
    type: Literal["arrivee", "depart", "promotion", "changement_poste"]
    date_approx: str = ""  # "YYYY-MM"
    contexte: str = ""  # max 15 words

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        """Normalize LLM output: strip accents and common variants."""
        import unicodedata
        if not isinstance(v, str):
            return v
        # Remove accents (départ → depart, arrivée → arrivee)
        nfkd = unicodedata.normalize("NFKD", v.strip().lower())
        cleaned = "".join(c for c in nfkd if not unicodedata.combining(c))
        # Map common LLM variants
        mapping = {
            "depart": "depart",
            "départ": "depart",
            "arrivee": "arrivee",
            "arrivée": "arrivee",
            "changement_poste": "changement_poste",
            "changement de poste": "changement_poste",
            "changement poste": "changement_poste",
        }
        return mapping.get(cleaned, cleaned)


class MapLotResult(BaseModel):
    lot_number: int
    company_name: str
    dirigeants: list[MapDirigeant] = Field(default_factory=list)
    posts_pertinents: list[MapPost] = Field(default_factory=list)
    posts_ignores_count: int = 0
    stack_detectee_lot: list[str] = Field(default_factory=list)
    mouvements_lot: list[MapMouvement] = Field(default_factory=list)


# --- REDUCE models (consolidated output) ---


class ReduceCLevel(BaseModel):
    name: str
    current_title: str
    anciennete_mois: Optional[int] = None
    role_deduit: str = ""  # CEO|CFO|CIO|CTO|CDO|COO|CMO|CHRO|VP_IT|VP_Digital|VP_Sales|VP_Transfo|VP_Operations|BU_Head|Autre
    pertinence_commerciale: int = 1  # 1-5


class OrgLink(BaseModel):
    de: str
    vers: str
    relation: Literal["reporte_a", "meme_comex", "mentionne_comme_equipe", "supervise"]
    confidence: Literal["high", "medium", "low"] = "medium"


class ThemeTransversal(BaseModel):
    theme: str
    count: int = 0
    auteurs: list[str] = Field(default_factory=list)


class StackEntry(BaseModel):
    outil: str
    source: str = ""  # "post", "profil", "headline", "offre"
    mentionne_par: str = ""


class PreSignal(BaseModel):
    signal_id: str
    probable: bool = False
    evidence: str = ""  # max 30 words
    source: str = ""  # person or post name


class ConsolidatedLinkedIn(BaseModel):
    """Output of the REDUCE step: all LinkedIn data consolidated."""
    company_name: str
    extraction_date: str = ""  # "YYYY-MM-DD"
    profils_total: int = 0
    profils_c_level: int = 0
    lots_fusionnes: int = 0

    dirigeants: list[MapDirigeant] = Field(default_factory=list)
    c_levels: list[ReduceCLevel] = Field(default_factory=list)
    organigramme_probable: list[OrgLink] = Field(default_factory=list)
    posts_pertinents: list[MapPost] = Field(default_factory=list)
    themes_transversaux: list[ThemeTransversal] = Field(default_factory=list)
    stack_consolidee: list[StackEntry] = Field(default_factory=list)
    mouvements_consolides: list[MapMouvement] = Field(default_factory=list)
    croissance_effectifs: Optional[dict] = None
    signaux_pre_detectes: list[PreSignal] = Field(default_factory=list)
