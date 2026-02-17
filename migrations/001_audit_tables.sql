-- Migration: Create audit tables for Company Audit Agent
-- Spec reference: Section 8.2

-- Table principale des audits
CREATE TABLE ai_agent_company_audit_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deal_id text NOT NULL,
  stage_id text NOT NULL,
  company_name text NOT NULL,
  domain text NOT NULL,
  linkedin_company_id text,
  linkedin_company_url text,
  linkedin_available boolean DEFAULT true,

  -- Inputs envoyés à chaque agent (pour debug/replay)
  input_finance jsonb,
  input_entreprise jsonb,
  input_dynamique jsonb,
  input_comex_organisation jsonb,
  input_comex_profils jsonb,
  input_connexions jsonb,

  -- Outputs JSON standard de chaque agent
  report_finance jsonb,
  report_entreprise jsonb,
  report_dynamique jsonb,
  report_comex_organisation jsonb,
  report_comex_profils jsonb,
  report_connexions jsonb,

  -- Scoring (calculé, stocké ici, PAS poussé dans HubSpot)
  scoring_signals jsonb,
  score_total integer,
  data_quality_score float,

  -- Rapport final
  final_report text,

  -- Metadata
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,

  UNIQUE (deal_id, stage_id)
);

-- Dirigeants identifiés pour un audit
CREATE TABLE ai_agent_company_audit_executives (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_id uuid NOT NULL REFERENCES ai_agent_company_audit_reports(id) ON DELETE CASCADE,
  deal_id text NOT NULL,
  domain text NOT NULL,
  linkedin_private_url text,
  linkedin_profile_url text,
  full_name text,
  headline text,
  current_job_title text,
  company_name text,
  is_current_employee boolean NOT NULL DEFAULT true,
  experiences jsonb,
  educations jsonb,
  skills jsonb,
  connected_with jsonb,
  enrichment_status text NOT NULL DEFAULT 'pending' CHECK (enrichment_status IN ('pending', 'enriched', 'cached', 'failed')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_executives_audit_id ON ai_agent_company_audit_executives(audit_id);
CREATE INDEX idx_audit_executives_domain ON ai_agent_company_audit_executives(domain);

-- Posts LinkedIn des dirigeants
CREATE TABLE ai_agent_company_audit_linkedin_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_id uuid NOT NULL REFERENCES ai_agent_company_audit_reports(id) ON DELETE CASCADE,
  linkedin_private_url text NOT NULL,
  full_name text,
  post_id text,
  post_url text,
  post_text text,
  published_at timestamptz,
  total_reactions integer DEFAULT 0,
  total_comments integer DEFAULT 0,
  total_reshares integer DEFAULT 0,
  is_reshare boolean DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_posts_audit_id ON ai_agent_company_audit_linkedin_posts(audit_id);
