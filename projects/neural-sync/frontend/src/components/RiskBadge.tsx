/**
 * RiskBadge — displays a colour-coded badge for burnout or bench risk.
 *
 * AC8 compliance:
 *   - Only renders risk badge LEVEL (low / medium / high) and optionally the
 *     numeric risk SCORE as a percentage.
 *   - Never renders raw behavioral vectors, work_style arrays, or motivation
 *     scalar values — those are explicitly excluded from the API response
 *     (GET /teams/{id}/risk-summary, GET /developers/{id}/risk).
 */

import type { CSSProperties } from 'react';
import type { RiskBadgeLevel } from '../api/client';

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface RiskBadgeProps {
  /** 'burnout' | 'bench' — determines the label text. */
  type: 'burnout' | 'bench';
  /** Badge severity level derived from the API's *_risk_badge field. */
  level: RiskBadgeLevel;
  /**
   * Optional numeric risk score (0.0 – 1.0) from the API's *_risk_score field.
   * Rendered as a percentage in parentheses when provided.
   * This is only the computed SCORE, never a raw behavioral vector.
   */
  score?: number;
}

// ─── Style maps ────────────────────────────────────────────────────────────────

const COLOR: Record<RiskBadgeLevel, string> = {
  low: '#15803d',
  medium: '#b45309',
  high: '#b91c1c',
};

const BACKGROUND: Record<RiskBadgeLevel, string> = {
  low: '#f0fdf4',
  medium: '#fef9c3',
  high: '#fef2f2',
};

const BORDER: Record<RiskBadgeLevel, string> = {
  low: '#86efac',
  medium: '#fcd34d',
  high: '#fca5a5',
};

const TYPE_LABEL: Record<'burnout' | 'bench', string> = {
  burnout: 'Burnout',
  bench: 'Bench',
};

// ─── Component ─────────────────────────────────────────────────────────────────

/**
 * RiskBadge renders a pill showing the risk type label and severity level.
 * Example output:  [ Burnout: HIGH (82%) ]
 */
export function RiskBadge({ type, level, score }: RiskBadgeProps) {
  const badgeStyle: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    padding: '3px 10px',
    borderRadius: '9999px',
    backgroundColor: BACKGROUND[level],
    color: COLOR[level],
    fontSize: '0.72rem',
    fontWeight: 700,
    border: `1px solid ${BORDER[level]}`,
    letterSpacing: '0.02em',
    whiteSpace: 'nowrap',
    userSelect: 'none',
  };

  const dotStyle: CSSProperties = {
    width: '6px',
    height: '6px',
    borderRadius: '50%',
    backgroundColor: COLOR[level],
    flexShrink: 0,
  };

  const scoreStyle: CSSProperties = {
    fontWeight: 400,
    opacity: 0.85,
  };

  return (
    <span
      data-testid={`risk-badge-${type}-${level}`}
      aria-label={`${TYPE_LABEL[type]} risk: ${level}${score !== undefined ? `, ${Math.round(score * 100)}%` : ''}`}
      style={badgeStyle}
    >
      <span style={dotStyle} aria-hidden="true" />
      {TYPE_LABEL[type]}: {level.toUpperCase()}
      {score !== undefined && (
        <span style={scoreStyle}>({Math.round(score * 100)}%)</span>
      )}
    </span>
  );
}
