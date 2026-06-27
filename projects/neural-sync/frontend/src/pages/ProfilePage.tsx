/**
 * ProfilePage — Identity Layer (§2.1) view for the authenticated operator.
 *
 *   • developer → their own operator profile (skills, experience, preferred
 *     stack, career goals, timezone, availability) + their burnout/bench risk.
 *   • manager   → an identity card (role + account id).
 *
 * Privacy (ADR-002 / AC8): raw work_style / motivation vectors are never
 * fetched or rendered — they are modeled internally only.
 */

import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import type { DeveloperProfile, RiskResponse } from '../api/client';
import { getDeveloperProfile, getDeveloperRisk } from '../api/client';
import { RiskBadge } from '../components/RiskBadge';

export interface ProfilePageProps {
  role: 'developer' | 'manager';
  /** developer profile UUID (developer role only). */
  developerId?: string | null;
  /** user account UUID. */
  userId: string;
}

const card: CSSProperties = {
  border: '1px solid #e5e7eb',
  borderRadius: '12px',
  padding: '24px',
  backgroundColor: '#ffffff',
  boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
};

const label: CSSProperties = {
  fontSize: '0.7rem',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  color: '#6b7280',
  marginBottom: '6px',
};

const chip: CSSProperties = {
  display: 'inline-block',
  backgroundColor: '#eff6ff',
  border: '1px solid #93c5fd',
  color: '#1d4ed8',
  borderRadius: '6px',
  padding: '3px 10px',
  fontSize: '0.78rem',
  fontWeight: 600,
  margin: '0 6px 6px 0',
};

const goalChip: CSSProperties = {
  ...chip,
  backgroundColor: '#f0fdf4',
  border: '1px solid #86efac',
  color: '#15803d',
};

function Field({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '18px' }}>
      <div style={label}>{title}</div>
      <div style={{ fontSize: '0.9rem', color: '#111827' }}>{children}</div>
    </div>
  );
}

export function ProfilePage({ role, developerId, userId }: ProfilePageProps) {
  const [profile, setProfile] = useState<DeveloperProfile | null>(null);
  const [risk, setRisk] = useState<RiskResponse | null>(null);
  const [loading, setLoading] = useState(role === 'developer');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (role !== 'developer' || !developerId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([getDeveloperProfile(developerId), getDeveloperRisk(developerId)])
      .then(([p, r]) => {
        if (!cancelled) {
          setProfile(p);
          setRisk(r);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load profile.');
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [role, developerId]);

  const wrap: CSSProperties = { padding: '24px', maxWidth: '760px', margin: '0 auto' };

  // ── Manager identity card ──
  if (role === 'manager') {
    return (
      <div style={wrap}>
        <h1 style={{ margin: '0 0 20px', fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
          Operator Profile
        </h1>
        <div style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '20px' }}>
            <div style={{ width: '56px', height: '56px', borderRadius: '50%', backgroundColor: '#1d4ed8', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 800 }}>
              ⚙
            </div>
            <div>
              <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#111827' }}>Manager</div>
              <div style={{ fontSize: '0.8rem', color: '#6b7280' }}>Workforce allocation operator</div>
            </div>
          </div>
          <Field title="Role">Manager — full team risk &amp; weight-tuning access</Field>
          <Field title="Account ID"><code style={{ fontSize: '0.8rem' }}>{userId}</code></Field>
          <Field title="Capabilities">
            <span style={chip}>Team risk dashboard</span>
            <span style={chip}>Reallocation suggestions</span>
            <span style={chip}>Match weight tuning</span>
          </Field>
        </div>
      </div>
    );
  }

  // ── Developer operator profile ──
  if (loading) {
    return <div style={{ padding: '48px', textAlign: 'center', color: '#6b7280' }}>Loading profile…</div>;
  }
  if (error || !profile) {
    return (
      <div style={{ ...wrap }}>
        <div style={{ padding: '24px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px', color: '#b91c1c' }}>
          <strong>Error:</strong> {error ?? 'Profile not available.'}
        </div>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <h1 style={{ margin: '0 0 20px', fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
        Operator Profile
      </h1>
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '24px' }}>
          <div style={{ width: '56px', height: '56px', borderRadius: '50%', backgroundColor: '#1d4ed8', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 800 }}>
            👤
          </div>
          <div>
            <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#111827' }}>
              {profile.experience_years} yrs · {profile.preferred_stack.slice(0, 2).join(' / ')}
            </div>
            <div style={{ fontSize: '0.8rem', color: '#6b7280' }}>
              {profile.timezone} · {profile.availability_hours} h/week available
            </div>
          </div>
        </div>

        <Field title="Skills">
          {profile.skills.map((s) => (
            <span key={s} style={chip}>{s}</span>
          ))}
        </Field>

        <Field title="Preferred Stack">
          {profile.preferred_stack.map((s) => (
            <span key={s} style={chip}>{s}</span>
          ))}
        </Field>

        <Field title="Career Goals">
          {profile.career_goals.map((g) => (
            <span key={g} style={goalChip}>{g}</span>
          ))}
        </Field>

        <div style={{ display: 'flex', gap: '32px', flexWrap: 'wrap' }}>
          <Field title="Experience">{profile.experience_years} years</Field>
          <Field title="Timezone">{profile.timezone}</Field>
          <Field title="Availability">{profile.availability_hours} h/week</Field>
          <Field title="Behavioral Signals">
            {profile.is_self_reported ? 'Self-reported' : 'Inferred'} · modeled internally (private)
          </Field>
        </div>

        {risk && (
          <div style={{ marginTop: '8px', paddingTop: '18px', borderTop: '1px solid #f3f4f6' }}>
            <div style={label}>My Risk Signals</div>
            <div style={{ display: 'flex', gap: '12px' }}>
              <RiskBadge type="burnout" level={risk.burnout_risk_badge} score={risk.burnout_risk_score} />
              <RiskBadge type="bench" level={risk.bench_risk_badge} score={risk.bench_risk_score} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
