/**
 * WeightConfigPage — manager-only weight tuning UI.
 *
 * Loads current weights via GET /config/weights and allows the manager to
 * adjust the five matching dimensions (w1–w5). Validates that the sum equals
 * 1.0 before allowing save. Updates via PUT /config/weights.
 */

import { useEffect, useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import type { WeightConfig } from '../api/client';
import { getWeights, updateWeights } from '../api/client';

const DIMENSIONS: { key: keyof Omit<WeightConfig, 'version'>; label: string }[] = [
  { key: 'w1', label: 'Skills compatibility (w1)' },
  { key: 'w2', label: 'Work style fit (w2)' },
  { key: 'w3', label: 'Motivation alignment (w3)' },
  { key: 'w4', label: 'Timezone overlap (w4)' },
  { key: 'w5', label: 'Growth potential (w5)' },
];

const inputStyle: CSSProperties = {
  width: '80px',
  padding: '6px 8px',
  border: '1px solid #d1d5db',
  borderRadius: '6px',
  fontSize: '0.875rem',
  textAlign: 'right',
};

export function WeightConfigPage() {
  const [weights, setWeights] = useState<Omit<WeightConfig, 'version'>>({
    w1: 0.30,
    w2: 0.25,
    w3: 0.20,
    w4: 0.15,
    w5: 0.10,
  });
  const [version, setVersion] = useState<number>(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    getWeights()
      .then((cfg) => {
        setWeights({ w1: cfg.w1, w2: cfg.w2, w3: cfg.w3, w4: cfg.w4, w5: cfg.w5 });
        setVersion(cfg.version);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load weights.');
        setLoading(false);
      });
  }, []);

  const total = (Object.values(weights) as number[]).reduce((s: number, v: number) => s + v, 0);
  const totalOk = Math.abs(total - 1.0) < 0.001;

  const handleChange = (key: keyof typeof weights, value: string) => {
    const num = parseFloat(value);
    if (!isNaN(num)) {
      setWeights((prev: Omit<WeightConfig, 'version'>) => ({ ...prev, [key]: Math.round(num * 1000) / 1000 }));
      setSuccess(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!totalOk) return;
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const cfg = await updateWeights(weights);
      setWeights({ w1: cfg.w1, w2: cfg.w2, w3: cfg.w3, w4: cfg.w4, w5: cfg.w5 });
      setVersion(cfg.version);
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save weights.');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '48px', textAlign: 'center', color: '#6b7280' }}>
        <p style={{ margin: 0 }}>Loading weight configuration…</p>
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', maxWidth: '520px', margin: '0 auto' }}>
      <header style={{ marginBottom: '24px' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
          Matching Weight Configuration
        </h1>
        <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: '0.875rem' }}>
          Version {version} — weights must sum to 1.00
        </p>
      </header>

      <form onSubmit={(e) => void handleSubmit(e)}>
        <div
          style={{
            backgroundColor: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: '10px',
            overflow: 'hidden',
            marginBottom: '16px',
          }}
        >
          {DIMENSIONS.map(({ key, label }, i) => (
            <div
              key={key}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '14px 16px',
                borderBottom: i < DIMENSIONS.length - 1 ? '1px solid #f3f4f6' : 'none',
              }}
            >
              <label style={{ fontSize: '0.875rem', color: '#374151', fontWeight: 500 }}>
                {label}
              </label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={weights[key]}
                  onChange={(e) => handleChange(key, e.target.value)}
                  style={inputStyle}
                />
                <span style={{ fontSize: '0.8rem', color: '#9ca3af', width: '36px' }}>
                  {(weights[key] * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          ))}
        </div>

        {/* Sum indicator */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '16px',
            fontSize: '0.875rem',
          }}
        >
          <span style={{ color: '#6b7280' }}>Sum:</span>
          <span
            style={{
              fontWeight: 700,
              color: totalOk ? '#15803d' : '#b91c1c',
              minWidth: '48px',
              textAlign: 'right',
            }}
          >
            {total.toFixed(3)}
          </span>
          {!totalOk && (
            <span style={{ color: '#b91c1c', fontSize: '0.78rem' }}>
              Must equal 1.000
            </span>
          )}
        </div>

        {error !== null && (
          <div
            style={{
              padding: '10px 14px',
              marginBottom: '12px',
              backgroundColor: '#fef2f2',
              border: '1px solid #fca5a5',
              borderRadius: '6px',
              color: '#b91c1c',
              fontSize: '0.875rem',
            }}
          >
            {error}
          </div>
        )}

        {success && (
          <div
            style={{
              padding: '10px 14px',
              marginBottom: '12px',
              backgroundColor: '#f0fdf4',
              border: '1px solid #86efac',
              borderRadius: '6px',
              color: '#15803d',
              fontSize: '0.875rem',
            }}
          >
            Weights saved. All new match scores will use the updated configuration.
          </div>
        )}

        <button
          type="submit"
          disabled={!totalOk || saving}
          style={{
            width: '100%',
            padding: '10px',
            backgroundColor: totalOk ? '#1d4ed8' : '#9ca3af',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            fontWeight: 700,
            fontSize: '0.875rem',
            cursor: totalOk ? 'pointer' : 'not-allowed',
            opacity: saving ? 0.7 : 1,
          }}
        >
          {saving ? 'Saving…' : 'Save Weights'}
        </button>
      </form>
    </div>
  );
}
