-- Migration: Rename ghost_genius_available to linkedin_available
-- Reason: vendor-agnostic naming (state already uses linkedin_available)

ALTER TABLE ai_agent_company_audit_reports
  RENAME COLUMN ghost_genius_available TO linkedin_available;
