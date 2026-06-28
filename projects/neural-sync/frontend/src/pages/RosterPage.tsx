/**
 * RosterPage (WS-B5/B7) — paginated, filterable developer roster for managing the fleet
 * at scale (the user's "manage 10k developers" requirement). Backed by GET /developers
 * (server-side pagination + indexed filters). AC8-safe: renders only display name, skills,
 * timezone, availability, embedding status, and cached risk badges — never raw vectors.
 */
import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { listDevelopers } from '../api/client';
import type { DeveloperListResponse } from '../api/client';
import { RiskBadge } from '../components/RiskBadge';

const PAGE_SIZE = 25;

const thStyle: CSSProperties = {
  padding: '10px 14px', textAlign: 'left', fontWeight: 700, color: '#374151',
  backgroundColor: '#f9fafb', borderBottom: '2px solid #e5e7eb', whiteSpace: 'nowrap',
};
const tdStyle: CSSProperties = {
  padding: '10px 14px', borderBottom: '1px solid #f3f4f6', verticalAlign: 'middle', fontSize: '0.85rem',
};
const inputStyle: CSSProperties = {
  padding: '7px 10px', border: '1px solid #d1d5db', borderRadius: '6px', fontSize: '0.85rem', outline: 'none',
};
const btnStyle = (disabled: boolean): CSSProperties => ({
  padding: '6px 14px', borderRadius: '6px', border: '1px solid #d1d5db',
  backgroundColor: disabled ? '#f3f4f6' : '#fff', color: disabled ? '#9ca3af' : '#111827',
  cursor: disabled ? 'not-allowed' : 'pointer', fontSize: '0.8rem', fontWeight: 600,
});

export function RosterPage() {
  const [data, setData] = useState<DeveloperListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [riskBadge, setRiskBadge] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listDevelopers({
      limit: PAGE_SIZE,
      offset,
      search: search || undefined,
      risk_badge: riskBadge || undefined,
    })
      .then((r) => { setData(r); setLoading(false); })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Failed to load roster');
        setLoading(false);
      });
  }, [offset, search, riskBadge]);

  useEffect(() => { load(); }, [load]);

  const applySearch = () => { setOffset(0); setSearch(searchInput.trim()); };
  const onRiskChange = (v: string) => { setOffset(0); setRiskBadge(v); };

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = offset + items.length;

  return (
    <div data-testid="roster-page" style={{ padding: '24px', maxWidth: '1040px', margin: '0 auto' }}>
      <header style={{ marginBottom: '20px' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
          Developer Roster
        </h1>
        <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: '0.875rem' }}>
          {total} developer{total !== 1 ? 's' : ''}
          {total > 0 && ` · showing ${pageStart}–${pageEnd}`}
        </p>
      </header>

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          data-testid="roster-search"
          placeholder="Search by name…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') applySearch(); }}
          style={{ ...inputStyle, minWidth: '220px' }}
        />
        <button onClick={applySearch} style={btnStyle(false)}>Search</button>
        <select
          data-testid="roster-risk-filter"
          value={riskBadge}
          onChange={(e) => onRiskChange(e.target.value)}
          style={inputStyle}
        >
          <option value="">All risk levels</option>
          <option value="high">Risk: High</option>
          <option value="medium">Risk: Medium</option>
          <option value="low">Risk: Low</option>
        </select>
      </div>

      {loading && <p style={{ color: '#6b7280', fontSize: '0.9rem' }}>Loading roster…</p>}
      {error !== null && (
        <div style={{ padding: '16px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px', color: '#b91c1c' }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {!loading && error === null && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table data-testid="roster-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Developer</th>
                  <th style={thStyle}>Skills</th>
                  <th style={thStyle}>TZ</th>
                  <th style={thStyle}>Avail</th>
                  <th style={thStyle}>Burnout</th>
                  <th style={thStyle}>Bench</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr><td style={tdStyle} colSpan={6}>No developers match the current filters.</td></tr>
                ) : items.map((d) => (
                  <tr key={d.developer_id} data-testid={`roster-row-${d.developer_id}`}>
                    <td style={{ ...tdStyle, fontWeight: 600, color: '#111827' }}>{d.display_name ?? '—'}</td>
                    <td style={tdStyle}>{(d.skills || []).slice(0, 4).join(', ')}</td>
                    <td style={tdStyle}>{d.timezone}</td>
                    <td style={tdStyle}>{d.availability_hours}h</td>
                    <td style={tdStyle}>
                      {d.burnout_risk_badge
                        ? <RiskBadge type="burnout" level={d.burnout_risk_badge} />
                        : <span style={{ color: '#9ca3af', fontSize: '0.72rem' }}>—</span>}
                    </td>
                    <td style={tdStyle}>
                      {d.bench_risk_badge
                        ? <RiskBadge type="bench" level={d.bench_risk_badge} />
                        : <span style={{ color: '#9ca3af', fontSize: '0.72rem' }}>—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '16px' }}>
            <button
              data-testid="roster-prev"
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              style={btnStyle(offset === 0)}
            >
              ← Prev
            </button>
            <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>
              {total > 0 ? `${pageStart}–${pageEnd} of ${total}` : '0 of 0'}
            </span>
            <button
              data-testid="roster-next"
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={data?.next_offset == null}
              style={btnStyle(data?.next_offset == null)}
            >
              Next →
            </button>
          </div>
        </>
      )}
    </div>
  );
}
