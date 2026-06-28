/**
 * ConnectorPicker — AC26
 *
 * Populated from GET /api/v1/ingestion/connectors.
 * Credential-gated connectors (availability === 'credential-gated') are
 * rendered as disabled with a tooltip stating the required credentials.
 */

import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import type { ConnectorInfo } from '../api/client';
import { listIngestionConnectors } from '../api/client';

// ─── Props ─────────────────────────────────────────────────────────────────────

export interface ConnectorPickerProps {
  /** Currently-selected connector, or null if none. */
  selected: ConnectorInfo | null;
  /** Called when the user selects a live (non-gated) connector. */
  onSelect: (connector: ConnectorInfo) => void;
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const baseButtonStyle: CSSProperties = {
  padding: '10px 16px',
  border: '2px solid #d1d5db',
  borderRadius: '10px',
  backgroundColor: '#ffffff',
  color: '#111827',
  cursor: 'pointer',
  fontSize: '0.875rem',
  fontWeight: 500,
  textAlign: 'left',
  position: 'relative',
  transition: 'border-color 0.15s, background-color 0.15s',
  minWidth: '120px',
};

const selectedButtonStyle: CSSProperties = {
  ...baseButtonStyle,
  border: '2px solid #1d4ed8',
  backgroundColor: '#eff6ff',
  color: '#1d4ed8',
  fontWeight: 700,
};

const disabledButtonStyle: CSSProperties = {
  ...baseButtonStyle,
  border: '2px solid #e5e7eb',
  backgroundColor: '#f9fafb',
  color: '#9ca3af',
  cursor: 'not-allowed',
  opacity: 0.75,
};

// Kind icon mapping
const KIND_ICON: Record<ConnectorInfo['kind'], string> = {
  file: '📄',
  network: '🌐',
};

// ─── Component ─────────────────────────────────────────────────────────────────

export function ConnectorPicker({ selected, onSelect }: ConnectorPickerProps) {
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    listIngestionConnectors()
      .then((data) => {
        if (!cancelled) {
          setConnectors(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load connectors.');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <p
        data-testid="connector-picker-loading"
        style={{ color: '#6b7280', fontSize: '0.875rem', margin: 0 }}
      >
        Loading connectors…
      </p>
    );
  }

  if (error !== null) {
    return (
      <p
        data-testid="connector-picker-error"
        style={{ color: '#b91c1c', fontSize: '0.875rem', margin: 0 }}
      >
        {error}
      </p>
    );
  }

  return (
    <div
      data-testid="connector-picker"
      style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'stretch' }}
    >
      {connectors.map((connector) => {
        const isGated = connector.availability === 'credential-gated';
        const isSelected = selected?.source === connector.source;

        // Tooltip: for gated connectors, list required credentials;
        // for live connectors, show the description.
        const tooltipText = isGated
          ? `Required credentials: ${(connector.required_credentials ?? []).join(', ') || 'credentials required'}`
          : (connector.description ?? connector.source);

        const buttonStyle = isGated
          ? disabledButtonStyle
          : isSelected
            ? selectedButtonStyle
            : baseButtonStyle;

        return (
          <button
            key={connector.source}
            data-testid={`connector-option-${connector.source}`}
            disabled={isGated}
            title={tooltipText}
            aria-label={
              isGated
                ? `${connector.display_name ?? connector.source} — disabled (${tooltipText})`
                : connector.display_name ?? connector.source
            }
            onClick={() => {
              if (!isGated) {
                onSelect(connector);
              }
            }}
            style={buttonStyle}
          >
            <span style={{ marginRight: '6px' }}>
              {KIND_ICON[connector.kind] ?? '🔌'}
            </span>
            <span>{connector.display_name ?? connector.source}</span>
            {isGated && (
              <span
                style={{
                  display: 'block',
                  fontSize: '0.68rem',
                  color: '#9ca3af',
                  marginTop: '2px',
                }}
              >
                🔒 Credentials required
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
