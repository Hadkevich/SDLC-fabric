PROJECT: NEURAL SYNC — Operator–Project Alignment Platform "Night City doesn't reward drift. It rewards alignment. This system ensures no operator, no dev, no asset ends up in the wrong war."

1. 🎯 MISSION OBJECTIVE
Build an AI-driven platform that:
 • Matches developers ↔ projects based on:
 ◦ Technical compatibility
 ◦ Work style & communication patterns
 ◦ Motivation & career intent
 ◦ Availability & time zone
 • Continuously re-optimizes allocation
 • Predicts:
 ◦ Bench risk
 ◦ Burnout risk
 ◦ Team mismatch probability
 • Recommends:
 ◦ Project transitions
 ◦ Skill growth paths
 ◦ Internal mobility

2. 🧩 SYSTEM ARCHITECTURE
Core Modules
2.1 Identity Layer (Operator Profiles)
Stores structured + behavioral data.
Schema: DeveloperProfile
Copy
 
{
  "id": "uuid",
  "skills": ["python", "react"],
  "experience_years": 5,
  "preferred_stack": ["ai", "backend"],
  "work_style": {
    "async_vs_sync": 0.8,
    "team_vs_individual": 0.6,
    "structure_vs_flexibility": 0.7
  },
  "motivation_vector": {
    "learning": 0.9,
    "stability": 0.4,
    "innovation": 0.8
  },
  "timezone": "UTC+1",
  "availability_hours": 40,
  "career_goals": ["move to ML", "lead role"],
  "history": [project_ids]
}


2.2 Project Genome (Project Profiles)
Schema: ProjectProfile
Copy
 
{
  "id": "uuid",
  "required_skills": ["python", "ml"],
  "team_structure": {
    "size": 6,
    "communication_style": "async-heavy"
  },
  "workload_intensity": 0.7,
  "innovation_level": 0.9,
  "timezone_overlap_required": 4,
  "duration_weeks": 24,
  "growth_opportunities": ["ml", "distributed systems"]
}


2.3 Matching Engine (Core AI)
Responsibility: Generate compatibility score between Developer ↔ Project
Matching Dimensions


Matching Score Formula
Copy
 
MATCH_SCORE =
  w1 * skill_score +
  w2 * workstyle_score +
  w3 * motivation_score +
  w4 * timezone_score +
  w5 * growth_score
 

Weights configurable via admin panel.

3. 🤖 AI LAYER (CLAUDE CODE AGENT INTEGRATION)
Claude agents must be used for:
3.1 Profile Enrichment
 • Convert raw CV / Slack / Git logs into structured vectors
 • Extract:
 ◦ Behavioral traits
 ◦ Preferred work modes
 ◦ Hidden skill signals
3.2 Recommendation Generation
Claude generates:
 • "Why this match works" explanation
 • Suggested transitions
 • Career path hints

Example Prompt Spec
Copy
 
SYSTEM:
You are a workforce optimization AI in a cyberpunk setting.
Prioritize long-term engagement, not short-term efficiency.

USER INPUT:
Developer Profile JSON
Project Profile JSON

TASK:
1. Evaluate compatibility
2. Output:
   - match_score (0–1)
   - explanation
   - risks
   - growth potential


4. 🔄 REAL-TIME OPTIMIZATION ENGINE
Features
4.1 Bench Prediction
 • Input:
 ◦ Project end dates
 ◦ Allocation schedule
 • Output:
 ◦ Risk score per developer
4.2 Burnout Detection
 • Signals:
 ◦ Over-allocation
 ◦ High workload projects
 ◦ Low motivation alignment
4.3 Reallocation Suggestions
 • Suggest:
 ◦ Internal moves
 ◦ Skill bridge projects

5. 📊 DATA PIPELINES
Sources:
 • HR systems
 • Git repositories
 • Jira / task trackers
 • Communication tools (Slack)
Processing:
 • ETL → vectorization → storage
Storage:
 • Structured DB: PostgreSQL
 • Vector DB: Pinecone / Weaviate

6. 🖥️ FRONTEND (CONTROL INTERFACE)
Views:
Developer View
 • Recommended projects
 • Match explanations
 • Growth paths
Manager View
 • Team composition health
 • Risk alerts
 • Allocation suggestions
Admin View
 • Weight tuning
 • System overrides

7. ⚙️ TECH STACK
 • Backend: Node.js / Python (FastAPI)
 • AI Layer: Claude API
 • Vector DB: Weaviate / Pinecone
 • Frontend: React
 • Infra: AWS / GCP

8. 🔐 NON-FUNCTIONAL REQUIREMENTS
 • GDPR compliance (critical in EU ops)
 • Explainable AI decisions
 • Latency < 500ms per match request
 • Scalable to 10k+ developers

9. 🧪 MVP SCOPE (PHASE 1)
Deliver:
 • Profile ingestion
 • Basic matching engine
 • Claude explanation layer
 • Simple dashboard

10. ⚠️ FAILURE CONDITIONS
System is considered failed if:
 • Matches are skill-only (no behavioral layer)
 • No explainability
 • Static allocation (no re-optimization)


• Developers reject recommendations >50%

11. 🧬 CYBERPUNK DIRECTIVE LAYER (TEAM CULTURE)
You are not building HR software. You are building a neural allocation system in a hostile economy.
​
 • Resist “just ship matching by skills” → that’s surrender
 • Every feature must answer: “Does this increase human-system alignment?”
 • Prioritize:
 ◦ Signal extraction over UI polish
 ◦ Intelligence over automation
 • If the system cannot explain why a match exists → it is broken

12. 🚀 DELIVERABLE FORMAT
Each team must produce:
 • Module implementation
 • API contracts
 • Claude prompt definitions
 • Test scenarios (good match vs bad match)