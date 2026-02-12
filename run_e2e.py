"""End-to-end test: invoke the audit graph for a company."""

import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from hat_yai.graph import graph


async def main():
    import uuid
    run_id = uuid.uuid4().hex[:8]
    input_data = {
        "deal_id": f"e2e_{run_id}",
        "stage_id": f"e2e_stage_{run_id}",
        "company_name": "Altavia",
        "domain": "altavia.com",
    }
    print(f"\n{'='*60}")
    print(f"Starting E2E audit for: {input_data['company_name']} ({input_data['domain']})")
    print(f"{'='*60}\n")

    result = await graph.ainvoke(input_data)

    print(f"\n{'='*60}")
    print(f"AUDIT COMPLETE — Status: {result.get('final_status', 'UNKNOWN')}")
    print(f"{'='*60}")
    print(f"\nAudit Report ID: {result.get('audit_report_id')}")
    print(f"Ghost Genius available: {result.get('ghost_genius_available')}")
    print(f"LinkedIn Company ID: {result.get('linkedin_company_id')}")
    print(f"Number of agent reports: {len(result.get('agent_reports', []))}")
    print(f"Node errors: {json.dumps(result.get('node_errors', {}), indent=2)}")

    scoring = result.get("scoring_result")
    if scoring:
        print(f"\nScoring: {json.dumps(scoring, indent=2, ensure_ascii=False)}")

    report = result.get("final_report")
    if report:
        print(f"\n{'='*60}")
        print("FINAL REPORT:")
        print(f"{'='*60}")
        print(report[:3000])
        if len(report) > 3000:
            print(f"\n... [truncated, total {len(report)} chars]")


if __name__ == "__main__":
    asyncio.run(main())
