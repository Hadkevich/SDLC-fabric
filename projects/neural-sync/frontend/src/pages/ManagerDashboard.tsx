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
import type { TeamRiskDistribution, TeamRiskMember, TeamRiskSummary } from '../api/client';
import { getTeamRiskSummary } from '../api/client';
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

interface MemberRowProps {
  /** AC8: only developer_id, badge levels, and numeric risk scores are rendered.
   *  No work_style, motivation_vector, or behavioral dimension data. */
  member: TeamRiskMember;
}

function MemberRow({ member }: MemberRowProps) {
  return (
    <tr data-testid={`member-row-${member.developer_id}`}>
      <td style={tdStyle}>
        <code style={{ fontSize: '0.78rem', color: '#374151', wordBreak: 'break-all' }}>
          {member.developer_id}
        </code>
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
    </tr>
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
                  <th style={thStyle}>Developer ID</th>
                  <th style={thStyle}>Burnout Risk</th>
                  <th style={thStyle}>Bench Risk</th>
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
