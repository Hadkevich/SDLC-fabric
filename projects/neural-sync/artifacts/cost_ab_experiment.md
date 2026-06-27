# Cost & Efficiency — Model A/B (log summarizer)

- **task:** Summarize the neural-sync SDLC event log into 3 bullets
- **generated:** 2026-06-27T22:42:38.155813+00:00

| Model | Cost $ | Out tok | Latency | Output (3-bullet summary) |
|---|--:|--:|--:|---|
| haiku | 0.0447 | 824 | 37s | - **8 distinct stages ran**: requirement_ingestion → task_decomposition → planning_architecture → code_generation → code_review → testing_validation → deployment → monitoring_feedback.<br><br>- **Retries and blocks**: code_generation timed out once then recovered; code_review failed 3 times (missing artifact, JSON error, security baseline) before success; testing_validation timed out once then … |
| sonnet | 0.1117 | 182 | 40s | - **7 distinct stages ran**: requirement_ingestion, task_decomposition, planning_architecture, code_generation, code_review, testing_validation, deployment, and monitoring_feedback (8 if counting monitoring_feedback separately).<br>- **Retries and blocks occurred in 4 stages**: code_generation timed out once (retry 1); code_review had 2 retries for missing artifact, then blocked twice more for … |
| opus | 0.1809 | 576 | 35s | - **Stages run:** 8 distinct stages executed — requirement_ingestion, task_decomposition, planning_architecture, code_generation, code_review, testing_validation, deployment, and monitoring_feedback.<br>- **Retries & blocks:** code_generation and testing_validation each hit one timeout retry; code_review retried repeatedly (missing/invalid review_report.json) and was blocked once on a possible … |

## Verdict
For the **log-summarization** task, **haiku** produced a comparable 3-bullet summary at **$0.0447** vs **$0.1809** for **opus** — about **4.0× cheaper**. The cheaper tier is good enough here, which is why mechanical/summary roles (devops, log/format) route to a small/fast model in the per-role strategy (SPEC §4 / scorecard §7.2). Frontier models are reserved for the hard reasoning roles (architect, reviewer).

> Note: per-call cost includes shared system-prompt cache overhead, so absolute values are dominated by fixed costs at this tiny task size; the **ratio** is the signal. Output quality is judged from the side-by-side summaries above.
