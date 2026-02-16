"""LangGraph DAG — Company Audit Agent.

This is the main graph file referenced by langgraph.json.
Exports `graph` as the compiled StateGraph.

Spec reference: Section 2 (architecture v2).

Execution flow:
  START → orchestrator
  orchestrator → [agent_finance, agent_entreprise, linkedin_enrichment]  (parallel)
  linkedin_enrichment → map_linkedin → reduce_linkedin → router_linkedin (sequential)
  router_linkedin → [agent_comex_organisation, agent_dynamique]      (parallel)
  agent_comex_organisation → [agent_comex_profils, agent_connexions]  (parallel)
  [all 5 leaf agents] → agent_scoring                                 (fan-in)
  agent_scoring → agent_synthesizer
  agent_synthesizer → END
"""

from langgraph.graph import StateGraph, START, END

from hat_yai.state import AuditState
from hat_yai.nodes.orchestrator import orchestrator_node
from hat_yai.nodes.linkedin_enrichment_node import linkedin_enrichment_node
from hat_yai.nodes.map_node import map_node
from hat_yai.nodes.reduce_node import reduce_node
from hat_yai.nodes.router_node import router_node
from hat_yai.nodes.agent_finance import agent_finance_node
from hat_yai.nodes.agent_entreprise import agent_entreprise_node
from hat_yai.nodes.agent_dynamique import agent_dynamique_node
from hat_yai.nodes.agent_comex_organisation import agent_comex_organisation_node
from hat_yai.nodes.agent_comex_profils import agent_comex_profils_node
from hat_yai.nodes.agent_connexions import agent_connexions_node
from hat_yai.nodes.agent_scoring import agent_scoring_node
from hat_yai.nodes.agent_synthesizer import agent_synthesizer_node

# --- Build the graph ---

builder = StateGraph(AuditState)

# Register all nodes
builder.add_node("orchestrator", orchestrator_node)
builder.add_node("linkedin_enrichment", linkedin_enrichment_node)
builder.add_node("map_linkedin", map_node)
builder.add_node("reduce_linkedin", reduce_node)
builder.add_node("router_linkedin", router_node)
builder.add_node("agent_finance", agent_finance_node)
builder.add_node("agent_entreprise", agent_entreprise_node)
builder.add_node("agent_dynamique", agent_dynamique_node)
builder.add_node("agent_comex_organisation", agent_comex_organisation_node)
builder.add_node("agent_comex_profils", agent_comex_profils_node)
builder.add_node("agent_connexions", agent_connexions_node)
builder.add_node("agent_scoring", agent_scoring_node)
builder.add_node("agent_synthesizer", agent_synthesizer_node)

# --- Entry point ---
builder.add_edge(START, "orchestrator")

# --- Parallel fan-out from orchestrator ---
builder.add_edge("orchestrator", "agent_finance")
builder.add_edge("orchestrator", "agent_entreprise")
builder.add_edge("orchestrator", "linkedin_enrichment")

# --- LinkedIn Enrichment → MAP → REDUCE → Router (sequential) ---
builder.add_edge("linkedin_enrichment", "map_linkedin")
builder.add_edge("map_linkedin", "reduce_linkedin")
builder.add_edge("reduce_linkedin", "router_linkedin")

# --- Router triggers LinkedIn-dependent agents ---
builder.add_edge("router_linkedin", "agent_comex_organisation")
builder.add_edge("router_linkedin", "agent_dynamique")

# --- COMEX Organisation triggers its dependents ---
builder.add_edge("agent_comex_organisation", "agent_comex_profils")
builder.add_edge("agent_comex_organisation", "agent_connexions")

# --- Fan-in: wait for ALL leaf agents before scoring ---
builder.add_edge(["agent_finance", "agent_entreprise", "agent_dynamique", "agent_comex_profils", "agent_connexions"], "agent_scoring")

# --- Sequential: scoring then synthesis ---
builder.add_edge("agent_scoring", "agent_synthesizer")

# --- End ---
builder.add_edge("agent_synthesizer", END)

# --- Compile ---
graph = builder.compile()
