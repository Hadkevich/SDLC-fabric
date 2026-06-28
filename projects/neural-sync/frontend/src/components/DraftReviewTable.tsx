/**
 * DraftReviewTable — AC26
 *
 * Scrollable table for reviewing IngestionSummary.drafts returned in
 * preview mode.  Renders display_name, email, source, extracted skills,
 * timezone, experience_years, availability_hours, and enrichment provenance
 * for each draft candidate.
 */

import type { CSSProperties } from 'react';
import type { IngestionDraft } from '../api/client';

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface DraftReviewTableProps {
  drafts: IngestionDraft[];
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const wrapperStyle: CSSProperties = {
  overflowY: 'auto',
  maxHeight: '360px',
  border: '1px solid #e5e7eb',
  borderRadius: '8px',
};

const tableStyle: CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: '0.8rem',
};

const thStyle: CSSProperties = {
  position: 'sticky',
  top: 0,
  padding: '8px 12px',
  textAlign: 'left',
  fontWeight: 700,
  color: '#374151',
  backgroundColor: '#f9fafb',
  borderBottom: '2px solid #e5e7eb',
  whiteSpace: 'nowrap',
  zIndex: 1,
};

const tdStyle: CSSProperties = {
  padding: '8px 12px',
  borderBottom: '1px solid #f3f4f6',
  verticalAlign: 'top',
  color: '#374151',
};

const emptyStyle: CSSProperties = {
  padding: '12px',
  color: '#9ca3af',
  fontSize: '0.8rem',
  fontStyle: 'italic',
};

// Provenance badge colours
const PROV_COLORS: Record<string, { bg: string; text: string }> = {
  llm: { bg: '#eff6ff', text: '#1d4ed8' },
  heuristic: { bg: '#f0fdf4', text: '#15803d' },
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function DraftReviewTable({ drafts }: DraftReviewTableProps) {
  if (drafts.length === 0) {
    return (
      <p data-testid="draft-review-empty" style={emptyStyle}>
        No draft profiles to review.
      </p>
    );
  }

  return (
    <div data-testid="draft-review-table-wrapper" style={wrapperStyle}>
      <table data-testid="draft-review-table" style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Email</th>
            <th style={thStyle}>Source</th>
            <th style={thStyle}>Skills</th>
            <th style={thStyle}>Timezone</th>
            <th style={thStyle}>Exp (yrs)</th>
            <th style={thStyle}>Hrs/wk</th>
            <th style={thStyle}>Provenance</th>
          </tr>
        </thead>
        <tbody>
          {drafts.map((draft, idx) => {
            const provColor = draft.provenance != null
              ? PROV_COLORS[draft.provenance]
              : null;

            return (
              <tr
                key={`draft-${idx}`}
                data-testid={`draft-row-${idx}`}
                style={{ backgroundColor: idx % 2 === 0 ? '#ffffff' : '#fafafa' }}
              >
                <td style={tdStyle}>
                  {draft.display_name ?? <span style={{ color: '#9ca3af' }}>—</span>}
                </td>
                <td style={tdStyle}>
                  {draft.email ?? <span style={{ color: '#9ca3af' }}>—</span>}
                </td>
                <td style={tdStyle}>
                  <span
                    style={{
                      display: 'inline-block',
                      padding: '2px 6px',
                      backgroundColor: '#f3f4f6',
                      borderRadius: '4px',
                      fontSize: '0.72rem',
                      fontWeight: 600,
                      color: '#6b7280',
                    }}
                  >
                    {draft.source}
                  </span>
                </td>
                <td style={{ ...tdStyle, maxWidth: '200px' }}>
                  {draft.skills.length > 0 ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                      {draft.skills.slice(0, 8).map((skill) => (
                        <span
                          key={skill}
                          style={{
                            display: 'inline-block',
                            padding: '1px 5px',
                            backgroundColor: '#e0f2fe',
                            borderRadius: '3px',
                            fontSize: '0.7rem',
                            color: '#0369a1',
                          }}
                        >
                          {skill}
                        </span>
                      ))}
                      {draft.skills.length > 8 && (
                        <span style={{ fontSize: '0.7rem', color: '#9ca3af' }}>
                          +{draft.skills.length - 8} more
                        </span>
                      )}
                    </div>
                  ) : (
                    <span style={{ color: '#9ca3af', fontSize: '0.72rem' }}>No skills extracted</span>
                  )}
                </td>
                <td style={tdStyle}>
                  {draft.timezone ?? <span style={{ color: '#9ca3af' }}>—</span>}
                </td>
                <td style={{ ...tdStyle, textAlign: 'center' }}>
                  {draft.experience_years ?? <span style={{ color: '#9ca3af' }}>—</span>}
                </td>
                <td style={{ ...tdStyle, textAlign: 'center' }}>
                  {draft.availability_hours ?? <span style={{ color: '#9ca3af' }}>—</span>}
                </td>
                <td style={tdStyle}>
                  {provColor != null ? (
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '2px 6px',
                        backgroundColor: provColor.bg,
                        color: provColor.text,
                        borderRadius: '4px',
                        fontSize: '0.72rem',
                        fontWeight: 600,
                      }}
                    >
                      {draft.provenance}
                    </span>
                  ) : (
                    <span style={{ color: '#9ca3af' }}>—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
