"""Fill live LLM explanations for any of user1's top-10 matches that are still
on a deterministic stub — so every visible developer-dashboard card shows real
"AI Analysis" during the demo. Safe to re-run; only touches stub records and
never disturbs ones that already have a claude_async explanation.

    docker exec -w /app neural-sync-backend-1 python scripts/fill_llm.py
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from src.db.session import AsyncSessionLocal
from src.db.models import MatchRecord, ProjectProfile, FeedbackRecord
from src.services.claude_service import ClaudeService, get_claude_service

USER1_DEV_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MatchRecord)
            .where(MatchRecord.developer_id == USER1_DEV_ID)
            .order_by(MatchRecord.match_score.desc())
            .limit(10)
        )).scalars().all()

        # sanity: surface any record that would spin forever in the UI
        pending = [r for r in rows if r.explanation_source == "stub_pending"]
        print(f"user1 top-10: {len(rows)} records, {len(pending)} stub_pending (should be 0).")

        proj_map = {
            p.id: p for p in (await db.execute(select(ProjectProfile))).scalars().all()
        }
        claude = get_claude_service()
        filled = 0
        for rec in rows:
            if rec.explanation_source == "claude_async":
                continue
            proj = proj_map[rec.project_id]
            ctx = ClaudeService.build_prompt_context(
                skill_score=rec.skill_score, workstyle_score=rec.workstyle_score,
                motivation_score=rec.motivation_score, timezone_score=rec.timezone_score,
                growth_score=rec.growth_score, match_score=rec.match_score,
                developer_career_goals=["ML model development", "feature engineering", "data science"],
                project_growth_opportunities=proj.growth_opportunities,
                developer_experience_years=8, project_name=proj.name,
                developer_timezone="America/New_York",
                project_timezone_overlap=proj.timezone_overlap_required,
            )
            try:
                text, source = await claude.generate_with_retry(ctx)
                if text and source == "claude_async":
                    expl, risks, growth = claude.parse_explanation_response(text)
                    rec.explanation = expl
                    rec.explanation_source = "claude_async"
                    rec.explanation_updated_at = datetime.now(timezone.utc)
                    if risks:
                        rec.risks = risks
                    if growth:
                        rec.growth_potential = growth
                    filled += 1
                    print(f"  filled: {proj.name}")
                else:
                    print(f"  still stub (LLM unavailable): {proj.name}")
            except Exception as exc:  # noqa: BLE001
                print(f"  error for {proj.name}: {exc}")

        await db.commit()

        # clean feedback so Accept/Reject works across recording takes
        fb = (await db.execute(
            select(FeedbackRecord).where(FeedbackRecord.developer_id == USER1_DEV_ID)
        )).scalars().all()
        for f in fb:
            await db.delete(f)
        await db.commit()

        final = (await db.execute(
            select(MatchRecord)
            .where(MatchRecord.developer_id == USER1_DEV_ID)
            .order_by(MatchRecord.match_score.desc())
            .limit(10)
        )).scalars().all()
        n_async = sum(1 for r in final if r.explanation_source == "claude_async")
        print(f"\nFilled {filled} this run. Top-10 now {n_async}/10 live LLM; "
              f"cleared {len(fb)} user1 feedback rows.")


if __name__ == "__main__":
    asyncio.run(main())
