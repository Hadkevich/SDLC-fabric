/**
 * ProjectCard — renders a single project recommendation card.
 *
 * AC7 compliance:
 *   - Displays match_score (formatted as a percentage).
 *   - Displays the Claude-generated (or stub) explanation string.
 *   - Polls GET /matches/{match_id}/explanation every 3 s while
 *     explanation_source === 'stub_pending' and shows a loading indicator.
 *   - Renders risks[] and growth_potential[] lists.
 *   - Exposes Accept / Reject feedback buttons (POST /matches/feedback).
 *
 * AC8 compliance:
 *   - Does NOT render work_style arrays, motivation_vector values,
 *     or any raw behavioral scalar from component_scores.
 *     (weights_snapshot and component_scores are intentionally omitted
 *      from the UI — they are stored in the MatchRecord for backend use only.)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { ExplanationSource, MatchRecord } from '../api/client';
import { getMatchExplanation, submitFeedback } from '../api/client';

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface ProjectCardProps {
  match: MatchRecord;
  /** The authenticated developer's UUID — required for the feedback payload. */
  developerId: string;
  onFeedbackSubmitted?: (matchId: string, accepted: boolean) => void;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function formatMatchScore(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}

function isExplanationFinal(source: ExplanationSource): boolean {
  return (
    source === 'claude_cached' ||
    source === 'claude_async' ||
    source === 'stub_permanent'
  );
}

// ─── Styles (plain objects — no CSS-in-JS dependency) ─────────────────────────

const card: CSSProperties = {
  border: '1px solid #e5e7eb',
  borderRadius: '10px',
  padding: '20px',
  backgroundColor: '#ffffff',
  boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
  display: 'flex',
  flexDirection: 'column',
  gap: '14px',
};

const headerRow: CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'flex-start',
  gap: '12px',
};

const scorePill: CSSProperties = {
  flexShrink: 0,
  backgroundColor: '#eff6ff',
  border: '1px solid #93c5fd',
  borderRadius: '8px',
  padding: '6px 14px',
  textAlign: 'center',
  minWidth: '80px',
};

const sectionLabel: CSSProperties = {
  fontSize: '0.7rem',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  color: '#6b7280',
  marginBottom: '4px',
};

const chip: CSSProperties = {
  display: 'inline-block',
  backgroundColor: '#f0fdf4',
  border: '1px solid #86efac',
  color: '#15803d',
  borderRadius: '4px',
  padding: '2px 8px',
  fontSize: '0.75rem',
  fontWeight: 500,
};

const riskText: CSSProperties = {
  fontSize: '0.8rem',
  color: '#92400e',
  margin: 0,
};

const btnBase: CSSProperties = {
  flex: 1,
  padding: '7px 16px',
  borderRadius: '6px',
  cursor: 'pointer',
  fontSize: '0.875rem',
  fontWeight: 600,
  border: 'none',
  transition: 'opacity 0.15s',
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function ProjectCard({ match, developerId, onFeedbackSubmitted }: ProjectCardProps) {
  const [explanation, setExplanation] = useState<string>(match.explanation);
  const [explanationSource, setExplanationSource] = useState<ExplanationSource>(
    match.explanation_source,
  );
  const [feedbackState, setFeedbackState] = useState<boolean | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);

  const pollingTimer = useRef<number | null>(null);

  // ── Poll for Claude explanation while stub_pending ──
  useEffect(() => {
    if (isExplanationFinal(explanationSource)) return;

    let active = true;

    const poll = async () => {
      try {
        const resp = await getMatchExplanation(match.match_id);
        if (!active) return;
        setExplanation(resp.explanation);
        setExplanationSource(resp.explanation_source);
        if (isExplanationFinal(resp.explanation_source)) {
          if (pollingTimer.current !== null) {
            clearInterval(pollingTimer.current);
            pollingTimer.current = null;
          }
        }
      } catch {
        // Silently ignore polling errors — the stub remains visible.
      }
    };

    pollingTimer.current = window.setInterval(poll, 3000);

    return () => {
      active = false;
      if (pollingTimer.current !== null) {
        clearInterval(pollingTimer.current);
        pollingTimer.current = null;
      }
    };
  }, [match.match_id, explanationSource]);

  // ── Submit feedback ──
  const handleFeedback = useCallback(
    async (accepted: boolean) => {
      setFeedbackError(null);
      try {
        await submitFeedback({
          developer_id: developerId,
          match_id: match.match_id,
          accepted,
        });
        setFeedbackState(accepted);
        onFeedbackSubmitted?.(match.match_id, accepted);
      } catch (err) {
        setFeedbackError(
          err instanceof Error ? err.message : 'Could not submit feedback.',
        );
      }
    },
    [developerId, match.match_id, onFeedbackSubmitted],
  );

  const isPending = !isExplanationFinal(explanationSource);

  return (
    <article data-testid="project-card" style={card}>

      {/* ── Header: project name / score ── */}
      <div style={headerRow}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            Project
          </h3>
          <p style={{ margin: '2px 0 0', fontSize: '0.72rem', color: '#9ca3af', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {match.project_id}
          </p>
        </div>

        {/* Match score — AC7: visible percentage */}
        <div data-testid="match-score" style={scorePill}>
          <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#1d4ed8', lineHeight: 1 }}>
            {formatMatchScore(match.match_score)}
          </div>
          <div style={{ fontSize: '0.62rem', color: '#60a5fa', marginTop: '2px' }}>
            Match
          </div>
        </div>
      </div>

      {/* ── Claude explanation — AC7 ── */}
      <div>
        <div style={{ ...sectionLabel, display: 'flex', alignItems: 'center', gap: '6px' }}>
          AI Analysis
          {isPending && (
            <span
              data-testid="explanation-loading"
              style={{ color: '#9ca3af', fontStyle: 'italic', textTransform: 'none', fontSize: '0.7rem', fontWeight: 400 }}
            >
              AI is generating a full analysis…
            </span>
          )}
        </div>
        <p
          data-testid="match-explanation"
          style={{
            margin: 0,
            fontSize: '0.875rem',
            color: isPending ? '#6b7280' : '#374151',
            lineHeight: 1.6,
          }}
        >
          {explanation}
        </p>
      </div>

      {/* ── Risk tags ── */}
      {match.risks.length > 0 && (
        <div>
          <div style={sectionLabel}>Risks</div>
          <ul style={{ margin: 0, paddingLeft: '16px' }}>
            {match.risks.map((risk, i) => (
              <li key={i} style={riskText}>{risk}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Growth potential ── */}
      {match.growth_potential.length > 0 && (
        <div>
          <div style={sectionLabel}>Growth Opportunities</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {match.growth_potential.map((item, i) => (
              <span key={i} style={chip}>{item}</span>
            ))}
          </div>
        </div>
      )}

      {/* ── Degradation notices ── */}
      {(match.vector_search_degraded || match.behavioral_data_unavailable) && (
        <div style={{ fontSize: '0.7rem', color: '#9ca3af', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          {match.vector_search_degraded && (
            <span>⚠ Vector search degraded — keyword fallback used</span>
          )}
          {match.behavioral_data_unavailable && (
            <span>⚠ Behavioral embeddings not yet available</span>
          )}
        </div>
      )}

      {/* ── Feedback controls ── */}
      {feedbackState === null ? (
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            data-testid="accept-button"
            onClick={() => void handleFeedback(true)}
            style={{ ...btnBase, backgroundColor: '#16a34a', color: '#fff' }}
          >
            ✓ Accept
          </button>
          <button
            data-testid="reject-button"
            onClick={() => void handleFeedback(false)}
            style={{ ...btnBase, backgroundColor: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db' }}
          >
            ✗ Reject
          </button>
        </div>
      ) : (
        <div
          data-testid="feedback-submitted"
          style={{
            textAlign: 'center',
            fontSize: '0.875rem',
            fontWeight: 700,
            color: feedbackState ? '#15803d' : '#b91c1c',
            padding: '6px',
            backgroundColor: feedbackState ? '#f0fdf4' : '#fef2f2',
            borderRadius: '6px',
          }}
        >
          {feedbackState ? '✓ Accepted' : '✗ Rejected'}
        </div>
      )}

      {feedbackError !== null && (
        <p style={{ margin: 0, color: '#ef4444', fontSize: '0.75rem' }}>{feedbackError}</p>
      )}
    </article>
  );
}
