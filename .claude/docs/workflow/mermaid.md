```mermaid
flowchart TD
  A["Requirement Ingestion\n🤖 product-agent"] --> B["Task Decomposition\n🤖 planner-agent"]
  B --> C["Planning & Architecture\n🤖 architect-agent"]
  C --> D["Code Generation\n🤖 developer-agent"]
  D --> E["Code Review\n🤖 reviewer-agent"]
  E -->|approved| F["Testing & Validation\n🤖 qa-agent"]
  E -->|blocked| D
  F -->|passed| G["Deployment\n🤖 devops-agent"]
  F -->|failed| D
  G --> H["Monitoring & Feedback\n🤖 orchestrator-agent"]
  H -->|new bug / regression| A
  H -->|scope change| B

  E -->|escalate| Z[Human Intervention]
  F -->|escalate| Z
  G -->|escalate| Z
```