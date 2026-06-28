# Cost & Efficiency Report

- **workflow_id:** `96fa0a8f-a908-4773-9363-15c3fc120443`
- **generated:** 2026-06-28T06:56:24.293964+00:00
- **total cost:** $47.1740 · **tokens:** 65,320,294 · **agent time:** 12916s
- **prompt cache:** 14,803,659 tok read · saved ≈ $39.8235
- **parallelism:** 1.10× (12916s agent-time in 11729s wall-clock)

| Agent role | Model | Runs | In tok | Out tok | Cost $ | Cache saved $ | Time | Coverage |
|---|---|--:|--:|--:|--:|--:|--:|---|
| developer-agent | sonnet | 6 | 24,469,753 | 198,601 | 14.731 | 0.0000 | 3508s | partial |
| architect-agent | opus | 4 | 2,670,813 | 224,413 | 7.752 | 0.0000 | 2902s | full |
| reviewer-agent | opus | 8 | 6,522,741 | 78,419 | 7.629 | 0.0000 | 1558s | partial |
| e2e-agent | sonnet | 2 | 15,648,279 | 67,291 | 7.471 | 39.8235 | 1791s | full |
| qa-agent | sonnet | 4 | 9,462,975 | 61,585 | 6.271 | 0.0000 | 1290s | partial |
| product-agent | sonnet | 2 | 950,027 | 35,407 | 1.502 | 0.0000 | 832s | full |
| planner-agent | sonnet | 2 | 215,851 | 38,747 | 1.022 | 0.0000 | 752s | full |
| devops-agent | haiku | 5 | 4,654,435 | 20,957 | 0.796 | 0.0000 | 283s | partial |
| orchestrator-agent | sonnet | 8 | 0 | 0 | 0.000 | 0.0000 | 0s | none |
| **TOTAL** | | 41 | 64,594,874 | 725,420 | 47.174 | 39.8235 | 12916s | |

**Parallel waves** (concurrent tasks within a stage)

| Stage | Tasks | Agent-time | Wall-clock | Speedup |
|---|--:|--:|--:|--:|
| planning_architecture | 4 | 2902s | 2182s | 1.33× |
| code_generation | 5 | 3508s | 3041s | 1.15× |

**Notes**
- orchestrator-agent: monitoring_feedback is orchestration logic — no LLM call
