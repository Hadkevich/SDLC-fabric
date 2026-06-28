/**
 * ProjectsPage — Admin View: Project Genome management (§2.2 / §6).
 *
 * Lists existing project profiles (GET /projects) and lets an admin add a new
 * one (POST /projects). New projects immediately become matchable / available
 * for reallocation. Edit/Delete are intentionally out of scope for this pass.
 */

import { useEffect, useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import type { ProjectProfile, ProjectCreate } from '../api/client';
import { listProjects, createProject } from '../api/client';

const inputStyle: CSSProperties = {
  padding: '8px 10px',
  border: '1px solid #d1d5db',
  borderRadius: '6px',
  fontSize: '0.85rem',
  width: '100%',
  boxSizing: 'border-box',
};

const labelStyle: CSSProperties = {
  display: 'block',
  fontSize: '0.78rem',
  fontWeight: 600,
  color: '#374151',
  marginBottom: '4px',
};

const cellStyle: CSSProperties = {
  padding: '8px 10px',
  fontSize: '0.82rem',
  color: '#374151',
  borderBottom: '1px solid #f3f4f6',
  textAlign: 'left',
  verticalAlign: 'top',
};

interface FormState {
  name: string;
  required_skills: string;
  team_structure: string;
  workload_intensity: string;
  innovation_level: string;
  timezone_overlap_required: string;
  duration_weeks: string;
  growth_opportunities: string;
}

const EMPTY_FORM: FormState = {
  name: '',
  required_skills: '',
  team_structure: 'async-heavy, size 6',
  workload_intensity: '0.6',
  innovation_level: '0.7',
  timezone_overlap_required: 'UTC+0 to UTC+3',
  duration_weeks: '24',
  growth_opportunities: '',
};

const toList = (csv: string): string[] =>
  csv.split(',').map((s) => s.trim()).filter((s) => s.length > 0);

export function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    listProjects()
      .then((rows) => {
        setProjects(rows);
        setLoading(false);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load projects.');
        setLoading(false);
      });
  };

  useEffect(load, []);

  const set = (key: keyof FormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSuccess(null);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    const skills = toList(form.required_skills);
    if (skills.length === 0) {
      setError('At least one required skill is needed.');
      return;
    }
    const wi = parseFloat(form.workload_intensity);
    const il = parseFloat(form.innovation_level);
    const dw = parseInt(form.duration_weeks, 10);
    if (isNaN(wi) || wi < 0 || wi > 1 || isNaN(il) || il < 0 || il > 1) {
      setError('Workload intensity and innovation level must be between 0 and 1.');
      return;
    }
    if (isNaN(dw) || dw < 1) {
      setError('Duration (weeks) must be a positive integer.');
      return;
    }

    const payload: ProjectCreate = {
      name: form.name.trim() || undefined,
      required_skills: skills,
      team_structure: form.team_structure.trim(),
      workload_intensity: wi,
      innovation_level: il,
      timezone_overlap_required: form.timezone_overlap_required.trim() || 'flexible',
      duration_weeks: dw,
      growth_opportunities: toList(form.growth_opportunities),
    };

    setSaving(true);
    try {
      const created = await createProject(payload);
      setSuccess(`Project "${created.name}" created.`);
      setForm(EMPTY_FORM);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create project.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
      <header style={{ marginBottom: '20px' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
          Project Genome
        </h1>
        <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: '0.875rem' }}>
          Admin View — create and review project profiles used by the matching engine.
        </p>
      </header>

      {/* ── Add Project form ── */}
      <form
        onSubmit={(e) => void handleSubmit(e)}
        style={{
          backgroundColor: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '10px',
          padding: '18px',
          marginBottom: '24px',
        }}
      >
        <h2 style={{ margin: '0 0 14px', fontSize: '1rem', fontWeight: 700, color: '#111827' }}>
          Add Project
        </h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '14px',
          }}
        >
          <div>
            <label style={labelStyle}>Name</label>
            <input style={inputStyle} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="ATLAS" />
          </div>
          <div>
            <label style={labelStyle}>Required skills (comma-separated) *</label>
            <input style={inputStyle} value={form.required_skills} onChange={(e) => set('required_skills', e.target.value)} placeholder="python, ml" />
          </div>
          <div>
            <label style={labelStyle}>Team structure</label>
            <input style={inputStyle} value={form.team_structure} onChange={(e) => set('team_structure', e.target.value)} placeholder="async-heavy, size 6" />
          </div>
          <div>
            <label style={labelStyle}>Timezone overlap required</label>
            <input style={inputStyle} value={form.timezone_overlap_required} onChange={(e) => set('timezone_overlap_required', e.target.value)} placeholder="UTC+0 to UTC+3" />
          </div>
          <div>
            <label style={labelStyle}>Workload intensity (0–1)</label>
            <input style={inputStyle} type="number" min={0} max={1} step={0.1} value={form.workload_intensity} onChange={(e) => set('workload_intensity', e.target.value)} />
          </div>
          <div>
            <label style={labelStyle}>Innovation level (0–1)</label>
            <input style={inputStyle} type="number" min={0} max={1} step={0.1} value={form.innovation_level} onChange={(e) => set('innovation_level', e.target.value)} />
          </div>
          <div>
            <label style={labelStyle}>Duration (weeks)</label>
            <input style={inputStyle} type="number" min={1} step={1} value={form.duration_weeks} onChange={(e) => set('duration_weeks', e.target.value)} />
          </div>
          <div>
            <label style={labelStyle}>Growth opportunities (comma-separated)</label>
            <input style={inputStyle} value={form.growth_opportunities} onChange={(e) => set('growth_opportunities', e.target.value)} placeholder="ml, distributed systems" />
          </div>
        </div>

        {error !== null && (
          <div style={{ marginTop: '12px', padding: '10px 14px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#b91c1c', fontSize: '0.85rem' }}>
            {error}
          </div>
        )}
        {success !== null && (
          <div style={{ marginTop: '12px', padding: '10px 14px', backgroundColor: '#f0fdf4', border: '1px solid #86efac', borderRadius: '6px', color: '#15803d', fontSize: '0.85rem' }}>
            {success}
          </div>
        )}

        <button
          type="submit"
          disabled={saving}
          style={{
            marginTop: '14px',
            padding: '10px 18px',
            backgroundColor: '#1d4ed8',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            fontWeight: 700,
            fontSize: '0.85rem',
            cursor: saving ? 'not-allowed' : 'pointer',
            opacity: saving ? 0.7 : 1,
          }}
        >
          {saving ? 'Creating…' : 'Create Project'}
        </button>
      </form>

      {/* ── Projects list ── */}
      <div style={{ backgroundColor: '#fff', border: '1px solid #e5e7eb', borderRadius: '10px', overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #e5e7eb', fontWeight: 700, fontSize: '0.9rem', color: '#111827' }}>
          Projects {loading ? '' : `(${projects.length})`}
        </div>
        {loading ? (
          <div style={{ padding: '24px', textAlign: 'center', color: '#6b7280' }}>Loading projects…</div>
        ) : projects.length === 0 ? (
          <div style={{ padding: '24px', textAlign: 'center', color: '#6b7280' }}>No projects yet — add one above.</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ backgroundColor: '#f9fafb' }}>
                <th style={{ ...cellStyle, fontWeight: 700, color: '#6b7280' }}>Name</th>
                <th style={{ ...cellStyle, fontWeight: 700, color: '#6b7280' }}>Required skills</th>
                <th style={{ ...cellStyle, fontWeight: 700, color: '#6b7280' }}>Intensity</th>
                <th style={{ ...cellStyle, fontWeight: 700, color: '#6b7280' }}>Innovation</th>
                <th style={{ ...cellStyle, fontWeight: 700, color: '#6b7280' }}>Weeks</th>
                <th style={{ ...cellStyle, fontWeight: 700, color: '#6b7280' }}>Growth</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td style={{ ...cellStyle, fontWeight: 600, color: '#111827' }}>{p.name}</td>
                  <td style={cellStyle}>{p.required_skills.join(', ')}</td>
                  <td style={cellStyle}>{p.workload_intensity.toFixed(2)}</td>
                  <td style={cellStyle}>{p.innovation_level.toFixed(2)}</td>
                  <td style={cellStyle}>{p.duration_weeks}</td>
                  <td style={cellStyle}>{p.growth_opportunities.join(', ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
