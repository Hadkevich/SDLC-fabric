"""Profile enrichment service — Task04-requirements §3.1 (Profile Enrichment).

Convert raw operator text (CV / Git logs / Slack) into the structured signals the
matching engine consumes: skills, an 8-dim work_style vector, an 8-dim
motivation_vector, and inferred career goals. This is the "raw logs → structured
vectors" path the spec calls for; the existing create-profile flow takes
self-reported vectors, this one *derives* them.

Two paths:
  * LLM path (provenance ``"llm"``) — loads the versioned prompt artifact
    ``artifacts/prompts/profile_enrichment_v1.json`` (no hardcoded prompt text) and
    calls the Gemini client, mirroring ``ClaudeService``. Used when GEMINI_API_KEY is
    set and the response parses into valid vectors.
  * Heuristic fallback (provenance ``"heuristic"``) — deterministic keyword/signal
    extraction. Always available (no key, no network). This is what runs in CI/dev
    and is the tested path.

The result is a *draft* with ``is_self_reported=False``: a human reviews it before it
becomes a DeveloperProfile. Scaffolding by design — real Slack/Git API connectors are
a documented follow-up; this accepts the already-extracted text blobs.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from src.core.settings import settings

logger = logging.getLogger(__name__)

# work_style dims (must match derive_project_work_style_vector in matching.py):
#   [collaboration, autonomy, structure, innovation, pace,
#    communication, risk_tolerance, remote_preference]
# motivation dims:
#   [impact, growth, compensation, stability, creativity,
#    recognition, autonomy, mission_alignment]
_WORK_STYLE_LEN = 8
_MOTIVATION_LEN = 8

# Canonical skills we can detect in free text. Multi-word/variant forms map to the
# same canonical token the matching alias map uses (react.js → react, etc.).
_SKILL_PATTERNS: dict[str, str] = {
    r"\bpython\b": "python", r"\bfastapi\b": "fastapi", r"\bdjango\b": "django",
    r"\bflask\b": "flask", r"\breact(?:\.?js)?\b": "react", r"\bvue(?:\.?js)?\b": "vue",
    r"\bangular\b": "angular", r"\bnode(?:\.?js)?\b": "node", r"\btypescript\b": "ts",
    r"\bjavascript\b": "js", r"\bpostgres(?:ql)?\b": "postgres", r"\bmysql\b": "mysql",
    r"\bmongo(?:db)?\b": "mongodb", r"\bredis\b": "redis", r"\bdocker\b": "docker",
    r"\bkubernetes\b|\bk8s\b": "k8s", r"\baws\b": "aws", r"\bgcp\b|\bgoogle cloud\b": "gcp",
    # "go" only as "golang" or "go <tech-word>" — bare \bgo\b matches the English verb
    # ("ready to go", "go-to engineer") and would inject a spurious Golang skill.
    r"\bazure\b": "azure",
    r"\bgolang\b|\bgo\b(?=\s+(?:developer|engineer|programming|lang|routines?|modules?|services?))": "go",
    r"\brust\b": "rust",
    # NB: trailing \b would never match after '+'/'#' (non-word chars), so c++/c# use a
    # leading boundary only.
    r"\bjava\b": "java", r"\bspring\b": "spring", r"\bc\+\+": "cpp", r"\bc#": "csharp",
    r"\bmachine learning\b|\bml\b": "ml", r"\bdeep learning\b": "ml",
    r"\bpytorch\b": "torch", r"\btensorflow\b": "tf", r"\bnlp\b": "nlp",
    r"\bml ?ops\b": "mlops", r"\bdata ?science\b": "data-science",
    r"\bgraphql\b": "graphql", r"\bgrpc\b": "grpc", r"\bkafka\b": "kafka",
    r"\bterraform\b": "terraform", r"\bsql\b": "sql",
}

# Signal phrases → (vector, index, delta). Each nudges one dimension from baseline.
# Deterministic and bounded; values are clamped to [0,1] after accumulation.
_WORK_STYLE_SIGNALS: list[tuple[str, int, float]] = [
    # 0 collaboration
    (r"\b(led|mentored|managed|pair[- ]?program|cross[- ]?functional|team lead)\b", 0, 0.3),
    (r"\b(solo|independent|individual contributor)\b", 0, -0.25),
    # 1 autonomy
    (r"\b(async|self[- ]?directed|ownership|autonomous)\b", 1, 0.3),
    # 2 structure
    (r"\b(agile|scrum|sprint|kanban|process|sox|compliance)\b", 2, 0.25),
    (r"\b(research|r&d|exploratory|greenfield|prototyp)\b", 2, -0.25),
    # 3 innovation
    (r"\b(research|novel|invent|prototyp|innovat|experiment)\b", 3, 0.3),
    # 4 pace
    (r"\b(startup|fast[- ]?paced|shipped|high[- ]?velocity|deadline)\b", 4, 0.3),
    # 5 communication
    (r"\b(documented|wrote docs|presented|spoke|blog|communicat|mentored)\b", 5, 0.25),
    # 6 risk_tolerance
    (r"\b(experiment|bet|0[- ]?to[- ]?1|greenfield|risk)\b", 6, 0.25),
    (r"\b(reliability|stability|maintain|legacy)\b", 6, -0.2),
    # 7 remote_preference
    (r"\b(remote|distributed|async|work from home|wfh)\b", 7, 0.3),
    (r"\b(on[- ]?site|in[- ]?office|hybrid)\b", 7, -0.2),
]

_MOTIVATION_SIGNALS: list[tuple[str, int, float]] = [
    # 0 impact
    (r"\b(impact|users|customers|outcomes|mission)\b", 0, 0.25),
    # 1 growth
    (r"\b(learn|grow|study|upskill|master|move to|transition)\b", 1, 0.3),
    # 2 compensation
    (r"\b(compensation|salary|equity|comp)\b", 2, 0.2),
    # 3 stability
    (r"\b(stable|stability|reliab|maintain|long[- ]?term|tenure)\b", 3, 0.3),
    (r"\b(startup|change|pivot)\b", 3, -0.2),
    # 4 creativity
    (r"\b(creativ|design|novel|invent|prototyp|innovat)\b", 4, 0.3),
    # 5 recognition
    (r"\b(led|principal|staff|senior|lead role|recognition|promoted)\b", 5, 0.25),
    # 6 autonomy
    (r"\b(autonom|ownership|self[- ]?directed|independent)\b", 6, 0.25),
    # 7 mission_alignment
    (r"\b(mission|purpose|social|sustainab|impact|meaning)\b", 7, 0.3),
]

_GOAL_PATTERNS = [
    r"(?:goal[s]?|want to|aspire to|aiming to|looking to|move to|transition to|grow into)[:\- ]+([^.\n;]{4,80})",
]


@dataclass
class EnrichmentResult:
    skills: list[str]
    work_style: list[float]
    motivation_vector: list[float]
    career_goals: list[str]
    provenance: str = "heuristic"
    preferred_stack: list[str] = field(default_factory=list)


def _clamp(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 4)


def _extract_skills(text: str) -> list[str]:
    found: list[str] = []
    for pattern, canonical in _SKILL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE) and canonical not in found:
            found.append(canonical)
    return found


def _apply_signals(text: str, length: int, signals: list[tuple[str, int, float]]) -> list[float]:
    vec = [0.5] * length  # neutral baseline
    for pattern, idx, delta in signals:
        if re.search(pattern, text, re.IGNORECASE):
            vec[idx] = vec[idx] + delta
    return [_clamp(v) for v in vec]


def _extract_goals(text: str) -> list[str]:
    goals: list[str] = []
    for pattern in _GOAL_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            phrase = m.group(1).strip().rstrip(".,;")
            if phrase and phrase.lower() not in (g.lower() for g in goals):
                goals.append(phrase)
    return goals[:5]


def _heuristic_enrich(combined_text: str) -> EnrichmentResult:
    """Deterministic, dependency-free extraction. Always succeeds."""
    skills = _extract_skills(combined_text)
    work_style = _apply_signals(combined_text, _WORK_STYLE_LEN, _WORK_STYLE_SIGNALS)
    motivation = _apply_signals(combined_text, _MOTIVATION_LEN, _MOTIVATION_SIGNALS)
    goals = _extract_goals(combined_text)
    return EnrichmentResult(
        skills=skills,
        work_style=work_style,
        motivation_vector=motivation,
        career_goals=goals,
        provenance="heuristic",
        preferred_stack=skills[:5],
    )


def _valid_vector(v: object, length: int) -> bool:
    return (
        isinstance(v, list)
        and len(v) == length
        and all(isinstance(x, (int, float)) and 0.0 <= float(x) <= 1.0 for x in v)
    )


def _llm_enrich(cv_text: str, git_log_text: str, slack_text: str) -> Optional[EnrichmentResult]:
    """LLM path via the versioned prompt artifact + Gemini. Returns None on any
    failure so the caller falls back to the heuristic path."""
    if not settings.gemini_api_key:
        return None
    try:
        from src.services.claude_service import PromptTemplate
        from pathlib import Path

        artifact = Path(settings.prompt_artifact_path).with_name("profile_enrichment_v1.json")
        prompt = PromptTemplate(artifact)
        if not prompt.is_loaded():
            return None
        filled = prompt.fill({
            "cv_text": cv_text or "(none)",
            "git_log_text": git_log_text or "(none)",
            "slack_text": slack_text or "(none)",
        })

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=prompt.model_name,
            contents=filled,
            config=types.GenerateContentConfig(
                max_output_tokens=1024,
                system_instruction=prompt.system_prompt or None,
            ),
        )
        raw = (resp.text or "").strip()
        # Strip accidental markdown fences before parsing.
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        if not _valid_vector(data.get("work_style"), _WORK_STYLE_LEN):
            return None
        if not _valid_vector(data.get("motivation_vector"), _MOTIVATION_LEN):
            return None
        skills = [str(s).lower().strip() for s in data.get("skills", []) if str(s).strip()]
        goals = [str(g).strip() for g in data.get("career_goals", []) if str(g).strip()]
        return EnrichmentResult(
            skills=skills,
            work_style=[_clamp(float(x)) for x in data["work_style"]],
            motivation_vector=[_clamp(float(x)) for x in data["motivation_vector"]],
            career_goals=goals[:5],
            provenance="llm",
            preferred_stack=skills[:5],
        )
    except Exception as exc:  # any failure → heuristic fallback
        logger.warning("LLM enrichment failed, falling back to heuristic: %s", exc)
        return None


def enrich_profile(
    cv_text: str,
    git_log_text: str = "",
    slack_text: str = "",
) -> EnrichmentResult:
    """Enrich raw operator text into structured matching signals.

    Tries the LLM path when a key is configured; always falls back to the
    deterministic heuristic path so it works offline / in CI.
    """
    combined = "\n".join(p for p in (cv_text, git_log_text, slack_text) if p)
    llm = _llm_enrich(cv_text, git_log_text, slack_text)
    if llm is not None:
        # Backfill skills/goals from heuristics if the model omitted them.
        if not llm.skills or not llm.career_goals:
            h = _heuristic_enrich(combined)
            llm.skills = llm.skills or h.skills
            llm.career_goals = llm.career_goals or h.career_goals
            llm.preferred_stack = llm.preferred_stack or h.preferred_stack
        return llm
    return _heuristic_enrich(combined)
