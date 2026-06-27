# Live Agentic SDLC Dashboard

A zero-dependency, auto-refreshing view of an agent run. It reads the artifacts
your agents already write — no changes to the agents required.

| It shows | Source file |
|----------|-------------|
| Agent pipeline, colored by status (pending / running / success / failure / blocked) | `workflow_state.json` |
| Event timeline (each agent, status, summary) | `events.log.jsonl` |
| "Under the agent" drill-down: files **produced** & **consumed** (clickable), summary, retries, blocking issues | both |

## Run it

```bash
./observability/serve.sh                 # defaults to project "tic-tac-toe" on :8777
./observability/serve.sh smart-expense-tracker
```

Then open the URL it prints, e.g.
`http://localhost:8777/observability/dashboard.html?project=tic-tac-toe`

The page polls the two files every ~1.5s, so as agents finish stages the boxes
recolor and new timeline rows appear automatically — no manual refresh.

> Don't open `dashboard.html` by double-clicking (`file://`). Browsers block
> local file reads, so auto-refresh won't work — always go through the server.

## Switching projects
Use the **project** dropdown in the header, or change the `?project=` query param.
The dropdown is **auto-discovered** from the `projects/` directory index (OBS-1), so a
new project appears with no code change. (`KNOWN_PROJECTS` in `dashboard.html` is only a
fallback for when the server's directory listing is disabled.)

## Live "running now" state
The deterministic orchestrator (`src/orchestrator/`) persists `status: in_progress`
to `workflow_state.json` **before** it invokes each stage's agent, so the running
box lights up yellow live — no `stage_start` event needed. Two checkpoint states
also render: `awaiting_approval` (blue — paused at a human sign-off, SPEC §8.6) and
`halted` (red — circuit breaker tripped). The dashboard polls `workflow_state.json`,
so these appear automatically as the engine advances.
