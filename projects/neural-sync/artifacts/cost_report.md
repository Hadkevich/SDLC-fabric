# Cost & Efficiency Report

- **workflow_id:** `96fa0a8f-a908-4773-9363-15c3fc120443`
- **generated:** 2026-06-27T23:39:09.252894+00:00
- **total cost:** $16.5084 · **tokens:** 16,174,870 · **agent time:** 5611s

| Agent role | Model | Runs | In tok | Out tok | Cost $ | Time | Coverage |
|---|---|--:|--:|--:|--:|--:|---|
| developer-agent | sonnet | 3 | 6,601,126 | 74,936 | 5.071 | 1331s | partial |
| reviewer-agent | opus | 7 | 5,079,365 | 58,692 | 4.983 | 1267s | partial |
| architect-agent | opus | 3 | 1,971,423 | 134,950 | 3.725 | 2034s | full |
| qa-agent | sonnet | 2 | 2,045,607 | 14,078 | 2.079 | 402s | partial |
| planner-agent | sonnet | 1 | 94,101 | 14,628 | 0.368 | 333s | full |
| product-agent | sonnet | 1 | 76,499 | 9,465 | 0.283 | 243s | full |
| devops-agent | haiku | 4 | 0 | 0 | 0.000 | 0s | none |
| orchestrator-agent | sonnet | 5 | 0 | 0 | 0.000 | 0s | none |
| **TOTAL** | | 26 | 15,868,121 | 306,749 | 16.508 | 5611s | |

**Notes**
- devops-agent: deployment is a mechanical/local step — no LLM usage recorded
- orchestrator-agent: monitoring_feedback is orchestration logic — no LLM call
