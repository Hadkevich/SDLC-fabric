/**
 * DeveloperDashboard — AC7 implementation.
 *
 * Fetches ranked project recommendations for the authenticated developer
 * via GET /developers/{developer_id}/matches and renders a ProjectCard for
 * each result.  Each card shows:
 *   • match_score (formatted as a percentage)                        [AC7]
 *   • Claude-generated explanation string (or stub with polling)    [AC7]
 *   • risks[], growth_potential[]
 *   • Accept / Reject feedback buttons
 *
 * AC8 compliance: no work_style vectors, motivation scalars, or raw
 * behavioral arrays are rendered — those fields are excluded from the
 * MatchRecord response by the backend.
 */

import { useEffect, useState } from 'react';
import type { DeveloperMatchesResponse } from '../api/client';
import { getDeveloperMatches } from '../api/client';
import { ProjectCard } from '../components/ProjectCard';

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface DeveloperDashboardProps {
  /** UUID of the authenticated developer (from LoginResponse.user_id). */
  developerId: string;
  /** Maximum number of recommendations to request (default 10). */
  topK?: number;
}

// ─── Component ─────────────────────────────────────────────────────────────────

export function DeveloperDashboard({ developerId, topK = 10 }: DeveloperDashboardProps) {
  const [data, setData] = useState<DeveloperMatchesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getDeveloperMatches(developerId, topK)
      .then((resp) => {
        if (!cancelled) {
          setData(resp);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load recommendations.');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [developerId, topK]);

  // ── Loading state ──
  if (loading) {
    return (
      <div
        data-testid="developer-dashboard-loading"
        style={{ padding: '48px', textAlign: 'center', color: '#6b7280' }}
      >
        <p style={{ margin: 0, fontSize: '0.9rem' }}>Loading project recommendations…</p>
      </div>
    );
  }

  // ── Error state ──
  if (error !== null) {
    return (
      <div
        data-testid="developer-dashboard-error"
        style={{
          padding: '24px',
          margin: '24px',
          backgroundColor: '#fef2f2',
          border: '1px solid #fca5a5',
          borderRadius: '8px',
          color: '#b91c1c',
        }}
      >
        <strong>Error:</strong> {error}
      </div>
    );
  }

  const matches = data?.matches ?? [];

  return (
    <div
      data-testid="developer-dashboard"
      style={{ padding: '24px', maxWidth: '840px', margin: '0 auto' }}
    >
      {/* ── Page header ── */}
      <header style={{ marginBottom: '24px' }}>
        <h1
          style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}
        >
          Your Project Recommendations
        </h1>
        {data !== null && (
          <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: '0.875rem' }}>
            {data.total} recommendation{data.total !== 1 ? 's' : ''} available
            {matches.length < data.total && ` · showing top ${matches.length}`}
          </p>
        )}
      </header>

      {/* ── Empty state ── */}
      {matches.length === 0 ? (
        <div
          data-testid="no-recommendations"
          style={{
            padding: '40px 24px',
            textAlign: 'center',
            backgroundColor: '#f9fafb',
            borderRadius: '10px',
            border: '1px dashed #d1d5db',
          }}
        >
          <p style={{ margin: 0, color: '#6b7280', fontSize: '0.9rem' }}>
            No project recommendations yet.
            <br />
            Ensure your developer profile is complete with all required fields to receive
            AI-powered matches.
          </p>
        </div>
      ) : (
        /* ── Recommendations list — AC7: ≥1 card with match_score + explanation ── */
        <div
          data-testid="recommendations-list"
          style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}
        >
          {matches.map((match) => (
            <ProjectCard
              key={match.match_id}
              match={match}
              developerId={developerId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
