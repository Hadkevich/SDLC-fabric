# Cost & Efficiency Report

- **workflow_id:** `96fa0a8f-a908-4773-9363-15c3fc120443`
- **generated:** 2026-06-28T04:00:22.052983+00:00
- **total cost:** $40.6602 · **tokens:** 50,271,907 · **agent time:** 11471s

| Agent role | Model | Runs | In tok | Out tok | Cost $ | Time | Coverage |
|---|---|--:|--:|--:|--:|--:|---|
| developer-agent | sonnet | 6 | 24,469,753 | 198,601 | 14.731 | 3508s | partial |
| architect-agent | opus | 4 | 2,670,813 | 224,413 | 7.752 | 2902s | full |
| reviewer-agent | opus | 8 | 6,522,741 | 78,419 | 7.629 | 1558s | partial |
| qa-agent | sonnet | 4 | 9,462,975 | 61,585 | 6.271 | 1290s | partial |
| product-agent | sonnet | 2 | 950,027 | 35,407 | 1.502 | 832s | full |
| planner-agent | sonnet | 2 | 215,851 | 38,747 | 1.022 | 752s | full |
| e2e-agent | sonnet | 1 | 649,247 | 17,936 | 0.957 | 346s | full |
| devops-agent | haiku | 5 | 4,654,435 | 20,957 | 0.796 | 283s | partial |
| orchestrator-agent | sonnet | 7 | 0 | 0 | 0.000 | 0s | none |
| **TOTAL** | | 39 | 49,595,842 | 676,065 | 40.660 | 11471s | |

**Notes**
- orchestrator-agent: monitoring_feedback is orchestration logic — no LLM call
