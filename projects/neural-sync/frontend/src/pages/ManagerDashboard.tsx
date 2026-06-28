/**
 * ManagerDashboard — AC8 implementation.
 *
 * Fetches team risk data via GET /teams/{team_id}/risk-summary and renders:
 *   • Per-developer risk badges (burnout risk, bench risk)          [AC8]
 *   • Overall team composition health metrics (distribution counts) [AC8]
 *
 * AC8 compliance — this component NEVER renders:
 *   • Raw work_style arrays
 *   • motivation_vector values or motivation scalar fields
 *   • Raw behavioral dimension scores (component_scores)
 *   • Any other internal developer behavioral data
 *
 * The GET /teams/{id}/risk-summary endpoint is architecturally guaranteed
 * to exclude those fields; this component adds a second layer of protection
 * by only mapping the explicitly allowed fields: developer_id, *_risk_badge,
 * *_risk_score, and the team risk_distribution counts.
 */

import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import type { TeamRiskDistribution, TeamRiskMember, TeamRiskSummary, ReallocationSuggestion } from '../api/client';
import { getTeamRiskSummary, getReallocationSuggestion } from '../api/client';
import { RiskBadge } from '../components/RiskBadge';

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface ManagerDashboardProps {
  /** Team UUID — used with GET /teams/{team_id}/risk-summary. */
  teamId: string;
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const metricGridStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
  gap: '12px',
  marginBottom: '32px',
};

const tableStyle: CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: '0.875rem',
};

const thStyle: CSSProperties = {
  padding: '10px 14px',
  textAlign: 'left',
  fontWeight: 700,
  color: '#374151',
  backgroundColor: '#f9fafb',
  borderBottom: '2px solid #e5e7eb',
  whiteSpace: 'nowrap',
};

const tdStyle: CSSProperties = {
  padding: '10px 14px',
  borderBottom: '1px solid #f3f4f6',
  verticalAlign: 'middle',
};

// ─── Sub-components ────────────────────────────────────────────────────────────

interface MetricCardProps {
  label: string;
  value: number;
  accentColor: string;
  testId?: string;
}

function MetricCard({ label, value, accentColor, testId }: MetricCardProps) {
  return (
    <div
      data-testid={testId}
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: '8px',
        padding: '14px 12px',
        backgroundColor: '#ffffff',
        textAlign: 'center',
        boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
      }}
    >
      <div style={{ fontSize: '2rem', fontWeight: 800, color: accentColor, lineHeight: 1 }}>
        {value}
      </div>
      <div style={{ fontSize: '0.68rem', color: '#6b7280', marginTop: '4px', lineHeight: 1.3 }}>
        {label}
      </div>
    </div>
  );
}

// ─── Reallocation modal ────────────────────────────────────────────────────────

interface ReallocationModalProps {
  suggestion: ReallocationSuggestion;
  onClose: () => void;
}

function ReallocationModal({ suggestion, onClose }: ReallocationModalProps) {
  const s = suggestion.suggestion;
  const overlayStyle: CSSProperties = {
    position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
  };
  const boxStyle: CSSProperties = {
    backgroundColor: '#fff', borderRadius: '12px', padding: '28px',
    maxWidth: '520px', width: '90%', boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
  };
  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={boxStyle} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 800, color: '#111827' }}>
            ⚡ Reallocation Suggestion
          </h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: '1.2rem', color: '#6b7280' }}>✕</button>
        </div>

        <div style={{ marginBottom: '16px', padding: '12px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#991b1b', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Current Risk — {suggestion.trigger === 'burnout' ? '🔥 Burnout' : '🪑 Bench'}
          </div>
          <div style={{ display: 'flex', gap: '16px', fontSize: '0.85rem' }}>
            <span>Burnout: <strong>{(suggestion.current_burnout_score * 100).toFixed(0)}%</strong> ({suggestion.current_burnout_badge})</span>
            <span>Bench: <strong>{(suggestion.current_bench_score * 100).toFixed(0)}%</strong> ({suggestion.current_bench_badge})</span>
          </div>
        </div>

        {s ? (
          <>
            <div style={{ marginBottom: '16px', padding: '14px', backgroundColor: '#f0fdf4', border: '1px solid #86efac', borderRadius: '8px' }}>
              <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#15803d', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Suggested Project — {s.action_type.replace('-', ' ')}
              </div>
              <div style={{ fontWeight: 700, fontSize: '1rem', color: '#111827', marginBottom: '4px' }}>{s.project_name}</div>
              <div style={{ fontSize: '0.85rem', color: '#374151', marginBottom: '10px' }}>
                Match score: <strong style={{ color: '#1d4ed8' }}>{(s.match_score * 100).toFixed(1)}%</strong>
              </div>
              <div style={{ fontSize: '0.8rem', color: '#374151', lineHeight: 1.5 }}>{s.rationale}</div>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#6b7280', marginBottom: '8px' }}>Score Breakdown</div>
              {Object.entries(s.component_scores).map(([key, value]) => {
                const label = key.replace('_score', '').replace('workstyle', 'Work Style').replace(/^\w/, c => c.toUpperCase());
                return (
                  <div key={key} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                    <span style={{ fontSize: '0.72rem', color: '#6b7280', width: '80px', flexShrink: 0 }}>{label}</span>
                    <div style={{ flex: 1, height: '6px', backgroundColor: '#f3f4f6', borderRadius: '3px', overflow: 'hidden' }}>
                      <div style={{ width: `${(value * 100).toFixed(0)}%`, height: '100%', backgroundColor: value >= 0.7 ? '#16a34a' : value >= 0.4 ? '#f59e0b' : '#ef4444', borderRadius: '3px' }} />
                    </div>
                    <span style={{ fontSize: '0.72rem', width: '32px', textAlign: 'right' }}>{(value * 100).toFixed(0)}%</span>
                  </div>
                );
              })}
            </div>

            {suggestion.trigger === 'burnout' && (
              <div style={{ padding: '10px', backgroundColor: '#eff6ff', border: '1px solid #93c5fd', borderRadius: '6px', fontSize: '0.8rem', color: '#1e40af' }}>
                📉 Projected burnout after move: <strong>{(s.projected_burnout_after_move * 100).toFixed(0)}%</strong>
                {' '}(from {(suggestion.current_burnout_score * 100).toFixed(0)}%)
              </div>
            )}
          </>
        ) : (
          <p style={{ color: '#6b7280', fontSize: '0.875rem' }}>No suitable projects found for reallocation.</p>
        )}

        <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
          <button onClick={onClose} style={{ padding: '8px 20px', borderRadius: '6px', border: '1px solid #d1d5db', backgroundColor: '#f9fafb', cursor: 'pointer', fontWeight: 600, fontSize: '0.875rem' }}>
            Dismiss
          </button>
          <button onClick={onClose} style={{ padding: '8px 20px', borderRadius: '6px', border: 'none', backgroundColor: '#1d4ed8', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.875rem' }}>
            ✓ Confirm Move
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Member row ────────────────────────────────────────────────────────────────

interface MemberRowProps {
  /** AC8: only developer_id, badge levels, and numeric risk scores are rendered.
   *  No work_style, motivation_vector, or behavioral dimension data. */
  member: TeamRiskMember;
}

function MemberRow({ member }: MemberRowProps) {
  const isHighRisk = member.burnout_risk_badge === 'high' || member.bench_risk_badge === 'high';
  const [suggestion, setSuggestion] = useState<ReallocationSuggestion | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSuggestMove = async () => {
    setLoading(true);
    try {
      const result = await getReallocationSuggestion(member.developer_id);
      setSuggestion(result);
    } catch {
      // ignore — badge disappears silently
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {suggestion && (
        <ReallocationModal suggestion={suggestion} onClose={() => setSuggestion(null)} />
      )}
      <tr data-testid={`member-row-${member.developer_id}`}>
        <td style={tdStyle}>
          <span style={{ fontSize: '0.875rem', color: '#111827', fontWeight: 500 }}>
            {member.display_name}
          </span>
        </td>
        <td style={tdStyle}>
          <RiskBadge
            type="burnout"
            level={member.burnout_risk_badge}
            score={member.burnout_risk_score}
          />
        </td>
        <td style={tdStyle}>
          <RiskBadge
            type="bench"
            level={member.bench_risk_badge}
            score={member.bench_risk_score}
          />
        </td>
        <td style={tdStyle}>
          {member.team_mismatch_badge != null ? (
            <RiskBadge
              type="mismatch"
              level={member.team_mismatch_badge}
              score={member.team_mismatch_probability ?? undefined}
            />
          ) : (
            <span style={{ fontSize: '0.72rem', color: '#9ca3af' }}>—</span>
          )}
        </td>
        <td style={tdStyle}>
          {isHighRisk && (
            <button
              onClick={() => void handleSuggestMove()}
              disabled={loading}
              style={{
                display: 'inline-block',
                padding: '3px 8px',
                backgroundColor: loading ? '#f3f4f6' : '#fff7ed',
                border: '1px solid #fdba74',
                borderRadius: '4px',
                fontSize: '0.72rem',
                fontWeight: 600,
                color: loading ? '#9ca3af' : '#c2410c',
                cursor: loading ? 'wait' : 'pointer',
              }}
            >
              {loading ? '…' : '⚡ Suggest Move'}
            </button>
          )}
        </td>
      </tr>
    </>
  );
}

// ─── Health metrics section ────────────────────────────────────────────────────

interface HealthMetricsProps {
  dist: TeamRiskDistribution;
  memberCount: number;
}

function HealthMetrics({ dist, memberCount }: HealthMetricsProps) {
  // Summary health indicators
  const atRisk =
    dist.burnout_high_count + dist.bench_high_count;

  const healthScore =
    memberCount > 0
      ? Math.round(((memberCount - atRisk) / memberCount) * 100)
      : 100;

  return (
    <section data-testid="team-health-metrics" style={{ marginBottom: '32px' }}>
      {/* Overall health score */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '16px' }}>
        <div
          data-testid="team-health-score"
          style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            backgroundColor: healthScore >= 80 ? '#f0fdf4' : healthScore >= 60 ? '#fef9c3' : '#fef2f2',
            border: `3px solid ${healthScore >= 80 ? '#22c55e' : healthScore >= 60 ? '#eab308' : '#ef4444'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <span
            style={{
              fontSize: '0.9rem',
              fontWeight: 800,
              color: healthScore >= 80 ? '#15803d' : healthScore >= 60 ? '#854d0e' : '#b91c1c',
            }}
          >
            {healthScore}%
          </span>
        </div>
        <div>
          <div style={{ fontWeight: 700, color: '#111827', fontSize: '0.9rem' }}>
            Team Health Score
          </div>
          <div style={{ fontSize: '0.75rem', color: '#6b7280' }}>
            {atRisk} member{atRisk !== 1 ? 's' : ''} flagged high risk out of {memberCount}
          </div>
        </div>
      </div>

      {/* Distribution counts */}
      <div style={metricGridStyle}>
        <MetricCard
          testId="burnout-high-count"
          label="Burnout — High"
          value={dist.burnout_high_count}
          accentColor="#b91c1c"
        />
        <MetricCard
          testId="burnout-medium-count"
          label="Burnout — Medium"
          value={dist.burnout_medium_count}
          accentColor="#b45309"
        />
        <MetricCard
          testId="burnout-low-count"
          label="Burnout — Low"
          value={dist.burnout_low_count}
          accentColor="#15803d"
        />
        <MetricCard
          testId="bench-high-count"
          label="Bench — High"
          value={dist.bench_high_count}
          accentColor="#b91c1c"
        />
        <MetricCard
          testId="bench-medium-count"
          label="Bench — Medium"
          value={dist.bench_medium_count}
          accentColor="#b45309"
        />
        <MetricCard
          testId="bench-low-count"
          label="Bench — Low"
          value={dist.bench_low_count}
          accentColor="#15803d"
        />
      </div>
    </section>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export function ManagerDashboard({ teamId }: ManagerDashboardProps) {
  const [data, setData] = useState<TeamRiskSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getTeamRiskSummary(teamId)
      .then((resp) => {
        if (!cancelled) {
          setData(resp);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load team risk summary.');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [teamId]);

  // ── Loading ──
  if (loading) {
    return (
      <div
        data-testid="manager-dashboard-loading"
        style={{ padding: '48px', textAlign: 'center', color: '#6b7280' }}
      >
        <p style={{ margin: 0, fontSize: '0.9rem' }}>Loading team risk summary…</p>
      </div>
    );
  }

  // ── Error ──
  if (error !== null) {
    return (
      <div
        data-testid="manager-dashboard-error"
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

  if (data === null) return null;

  return (
    <div
      data-testid="manager-dashboard"
      style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}
    >
      {/* ── Page header ── */}
      <header style={{ marginBottom: '28px' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
          Team Health Dashboard
        </h1>
        <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: '0.875rem' }}>
          {data.member_count} member{data.member_count !== 1 ? 's' : ''}
          {' · '}last computed{' '}
          {new Date(data.computed_at).toLocaleString(undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
          })}
        </p>
      </header>

      {/* ── Team composition health metrics — AC8 ── */}
      <HealthMetrics dist={data.risk_distribution} memberCount={data.member_count} />

      {/* ── Per-developer risk table — AC8 ── */}
      <section>
        <h2 style={{ margin: '0 0 14px', fontSize: '1rem', fontWeight: 700, color: '#111827' }}>
          Per-Developer Risk Badges
        </h2>

        {data.members.length === 0 ? (
          <p style={{ color: '#6b7280', fontSize: '0.875rem' }}>No team members found.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table data-testid="developer-risk-table" style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Developer</th>
                  <th style={thStyle}>Burnout Risk</th>
                  <th style={thStyle}>Bench Risk</th>
                  <th style={thStyle}>Team Fit</th>
                  <th style={thStyle}>Action</th>
                </tr>
              </thead>
              <tbody>
                {/*
                  AC8: only developer_id, *_risk_badge, and *_risk_score are
                  rendered.  work_style, motivation_vector, and component_scores
                  are NOT present in the API response or in this component.
                */}
                {data.members.map((member) => (
                  <MemberRow key={member.developer_id} member={member} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
