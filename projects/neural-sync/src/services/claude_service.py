"""LLM Explanation Service.

Loads the versioned prompt template from artifacts/prompts/match_explanation_v1.json
at startup and uses it to generate natural-language match explanations via the
Google Gemini API (gemini-2.5-flash — free tier). The model id lives in the prompt
artifact, so swapping models requires no source change. The class name ClaudeService
and the claude_cached/claude_async source labels are retained for back-compat with the
DB schema and API consumers.

AC12 enforcement:
  - NO prompt strings are hardcoded in this file.
  - All prompt content is loaded from the JSON artifact file.
  - Changing artifacts/prompts/match_explanation_v1.json does NOT require
    modifying any .py source file.
  - The prompt artifact path is referenced in code_spec.json.

The service provides:
  1. A synchronous stub explanation (< 10ms) for immediate response.
  2. An asynchronous Claude call that replaces the stub in MatchRecord.
  3. ExplanationCache lookup to avoid redundant Claude API calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Optional

from src.core.settings import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt template loader
# ─────────────────────────────────────────────────────────────────────────────

class PromptTemplate:
    """
    Holds the content of a versioned prompt artifact JSON file.
    Reload at runtime via SIGHUP without restarting the service.
    """

    def __init__(self, artifact_path: str | Path):
        self.artifact_path = Path(artifact_path)
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        """Load (or reload) the prompt template from the artifact JSON file."""
        try:
            with open(self.artifact_path, encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info(
                "Prompt template loaded: key=%s version=%s model=%s path=%s",
                self._data.get("prompt_key"),
                self._data.get("version"),
                self._data.get("model_name"),
                self.artifact_path,
            )
        except FileNotFoundError:
            logger.error(
                "Prompt artifact not found at %s — Claude explanations will be unavailable.",
                self.artifact_path,
            )
            self._data = {}
        except json.JSONDecodeError as exc:
            logger.error("Prompt artifact JSON parse error: %s", exc)
            self._data = {}

    @property
    def template_text(self) -> str:
        return self._data.get("template_text", "")

    @property
    def system_prompt(self) -> str:
        return self._data.get("system_prompt", "")

    @property
    def model_name(self) -> str:
        return self._data.get("model_name", "gemini-2.5-flash")

    @property
    def version(self) -> int:
        return int(self._data.get("version", 1))

    @property
    def prompt_key(self) -> str:
        return self._data.get("prompt_key", "match_explanation")

    def is_loaded(self) -> bool:
        return bool(self._data)

    def fill(self, context: dict[str, str]) -> str:
        """
        Substitute {placeholder} tokens in the template_text with context values.
        Only the keys defined in the artifact's `placeholders` list are allowed.
        """
        try:
            return self.template_text.format(**context)
        except KeyError as exc:
            logger.warning("Missing placeholder in prompt context: %s", exc)
            # Fill remaining placeholders with safe defaults
            safe_context = {k: context.get(k, "[N/A]") for k in self._data.get("placeholders", [])}
            return self.template_text.format_map(_SafeDict(safe_context))


class _SafeDict(dict):
    """dict subclass that returns '{key}' for missing keys during format_map."""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


# ─────────────────────────────────────────────────────────────────────────────
# Claude service
# ─────────────────────────────────────────────────────────────────────────────

class ClaudeService:
    """
    Manages Claude API calls for match explanations.

    The prompt template is loaded from the artifact JSON file specified in
    settings.prompt_artifact_path. No prompt text is ever hardcoded here.
    """

    def __init__(self, prompt_artifact_path: Optional[str] = None) -> None:
        path = prompt_artifact_path or settings.prompt_artifact_path
        self.prompt = PromptTemplate(path)
        self._semaphore = asyncio.Semaphore(settings.claude_max_concurrent)
        self._queue_depth: int = 0

        # Reload prompt on SIGHUP (zero-downtime prompt updates)
        try:
            signal.signal(signal.SIGHUP, self._handle_sighup)
        except (OSError, ValueError):
            # Windows or restricted environment — skip SIGHUP
            pass

    def _handle_sighup(self, signum: int, frame: object) -> None:
        self.prompt.load()

    @property
    def queue_depth(self) -> int:
        return self._queue_depth

    @property
    def queue_limit_active(self) -> bool:
        return self._semaphore.locked()

    # ── Cache key computation ─────────────────────────────────────────────

    @staticmethod
    def compute_cache_key(
        developer_profile_json: str,
        project_profile_json: str,
        weights_snapshot_json: str,
    ) -> str:
        """
        SHA-256(dev_hash + proj_hash + weights_hash).
        Changes in any of the three inputs invalidate the cache entry.
        """
        combined = (
            hashlib.sha256(developer_profile_json.encode()).hexdigest()
            + hashlib.sha256(project_profile_json.encode()).hexdigest()
            + hashlib.sha256(weights_snapshot_json.encode()).hexdigest()
        )
        return hashlib.sha256(combined.encode()).hexdigest()

    # ── Prompt context builder ───────────────────────────────────────────

    @staticmethod
    def build_prompt_context(
        *,
        skill_score: float,
        workstyle_score: float,
        motivation_score: float,
        timezone_score: float,
        growth_score: float,
        match_score: float,
        developer_career_goals: list[str],
        project_growth_opportunities: list[str],
        developer_experience_years: int,
        project_name: str,
        developer_timezone: str,
        project_timezone_overlap: str,
    ) -> dict[str, str]:
        """
        Build the context dict for prompt template substitution.
        IMPORTANT: Raw work_style_vector and motivation_vector are NEVER included here.
        Only computed aggregate scores and metadata are passed to Claude.
        """
        return {
            "skill_score": f"{skill_score * 100:.0f}",
            "workstyle_score": f"{workstyle_score * 100:.0f}",
            "motivation_score": f"{motivation_score * 100:.0f}",
            "timezone_score": f"{timezone_score * 100:.0f}",
            "growth_score": f"{growth_score * 100:.0f}",
            "match_score": f"{match_score * 100:.0f}",
            "developer_career_goals": "; ".join(developer_career_goals),
            "project_growth_opportunities": "; ".join(project_growth_opportunities),
            "developer_experience_years": str(developer_experience_years),
            "project_name": project_name,
            "developer_timezone": developer_timezone,
            "project_timezone_overlap": project_timezone_overlap,
        }

    # ── Claude API call ──────────────────────────────────────────────────

    async def generate_explanation(self, context: dict[str, str]) -> str:
        """
        Call the Gemini API with the versioned prompt template.
        Uses the prompt loaded from the artifact file — NO inline strings.

        Raises RuntimeError if the LLM API key is not configured.
        """
        if not self.prompt.is_loaded():
            raise RuntimeError("Prompt template not loaded — cannot call the LLM API")

        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set — cannot call the LLM API "
                "(get a free key at https://aistudio.google.com/app/apikey)"
            )

        filled_prompt = self.prompt.fill(context)

        self._queue_depth += 1
        try:
            async with self._semaphore:
                # Run the synchronous Gemini client in a thread pool
                result = await asyncio.to_thread(
                    self._call_llm_sync,
                    filled_prompt,
                    self.prompt.system_prompt,
                    self.prompt.model_name,
                )
        finally:
            self._queue_depth -= 1

        return result

    def _call_llm_sync(
        self, prompt_text: str, system_prompt: str, model_name: str
    ) -> str:
        """Synchronous wrapper for the Google Gemini SDK (runs in thread pool)."""
        from google import genai  # lazy import — not required if LLM is unused
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        config = types.GenerateContentConfig(
            max_output_tokens=1024,
            system_instruction=system_prompt or None,
        )
        response = client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config=config,
        )
        return response.text

    # ── Async explanation with retry ─────────────────────────────────────

    async def generate_with_retry(
        self,
        context: dict[str, str],
        max_attempts: int = 3,
    ) -> tuple[str, str]:
        """
        Generate Claude explanation with exponential backoff retry.
        Returns (explanation_text, explanation_source).
        """
        import asyncio as _asyncio

        delays = [1.0, 2.0, 4.0]  # 1s, 2s, 4s (max 3 attempts)
        last_error: Optional[Exception] = None

        for attempt, delay in enumerate(delays[:max_attempts], start=1):
            try:
                explanation = await self.generate_explanation(context)
                source = "claude_async"
                return explanation, source
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Claude API attempt %d/%d failed: %s", attempt, max_attempts, exc
                )
                if attempt < max_attempts:
                    await _asyncio.sleep(delay)

        logger.error("All Claude retry attempts exhausted: %s", last_error)
        return "", "stub_permanent"

    # ── Parse Claude response into structured fields ─────────────────────

    @staticmethod
    def parse_explanation_response(
        response_text: str,
    ) -> tuple[str, list[str], list[str]]:
        """
        Parse the Claude response into (explanation, risks, growth_potential).

        The prompt instructs Claude to produce:
          Section 1: Skill Alignment — [...]
          Section 2: Behavioral Fit — [...]
          Section 3: Growth Potential — [...]
          Risks: [...]
          Growth Opportunities: [...]
        """
        import re

        explanation_parts: list[str] = []
        risks: list[str] = []
        growth_potential: list[str] = []

        # Extract sections
        s1 = re.search(r"Section 1[:\s]+Skill Alignment\s*[—–-]+\s*(.+?)(?=Section 2|Risks:|Growth|$)", response_text, re.DOTALL | re.IGNORECASE)
        s2 = re.search(r"Section 2[:\s]+Behavioral Fit\s*[—–-]+\s*(.+?)(?=Section 3|Risks:|Growth|$)", response_text, re.DOTALL | re.IGNORECASE)
        s3 = re.search(r"Section 3[:\s]+Growth Potential\s*[—–-]+\s*(.+?)(?=Risks:|Growth Opportunities:|$)", response_text, re.DOTALL | re.IGNORECASE)

        if s1:
            explanation_parts.append(f"Skill alignment: {s1.group(1).strip()}")
        if s2:
            explanation_parts.append(f"Behavioral fit: {s2.group(1).strip()}")
        if s3:
            explanation_parts.append(f"Growth potential: {s3.group(1).strip()}")

        # Extract risks
        risks_match = re.search(r"Risks:\s*(.+?)(?=Growth Opportunities:|$)", response_text, re.DOTALL | re.IGNORECASE)
        if risks_match:
            risks_text = risks_match.group(1).strip()
            if "none identified" not in risks_text.lower():
                risks = [r.strip(" -•*") for r in risks_text.split(",") if r.strip(" -•*")]

        # Extract growth opportunities
        growth_match = re.search(r"Growth Opportunities:\s*(.+?)$", response_text, re.DOTALL | re.IGNORECASE)
        if growth_match:
            growth_text = growth_match.group(1).strip()
            growth_potential = [g.strip(" -•*") for g in growth_text.split(",") if g.strip(" -•*")]

        # If parsing failed, use the entire response as explanation
        if not explanation_parts:
            explanation = response_text.strip()
        else:
            explanation = " ".join(explanation_parts)

        # Ensure explanation is ≥ 50 chars
        if len(explanation) < 50:
            explanation = explanation + " " + response_text[:100]

        return explanation[:2000], risks[:5], growth_potential[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Singleton instance (lazy-initialized)
# ─────────────────────────────────────────────────────────────────────────────

_claude_service_instance: Optional[ClaudeService] = None


def get_claude_service() -> ClaudeService:
    """Return the singleton ClaudeService instance."""
    global _claude_service_instance
    if _claude_service_instance is None:
        _claude_service_instance = ClaudeService()
    return _claude_service_instance
