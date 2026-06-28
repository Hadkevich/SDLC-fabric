/**
 * IngestionPage — AC26
 *
 * Manager-only data ingestion page wired into the existing tab pattern
 * (no react-router; rendered by App.tsx when managerTab === 'ingestion').
 *
 * Features:
 *   • ConnectorPicker populated from GET /api/v1/ingestion/connectors
 *   • File dropzone for file-kind connectors (CSV, JSON, TXT, MD)
 *   • GitLab input form for the gitlab connector
 *   • Jira input form for the jira connector (credential-gated, disabled picker)
 *   • Preview button → POST with mode=preview → DraftReviewTable
 *   • Approve and Create button → POST with mode=commit → shows created count
 */

import { useRef, useState } from 'react';
import type { CSSProperties, DragEvent } from 'react';
import type { ConnectorInfo, GitlabIngestionRequest, IngestionSummary, JiraIngestionRequest } from '../api/client';
import { ingestGitlab, ingestJira, uploadFile } from '../api/client';
import { ConnectorPicker } from '../components/ConnectorPicker';
import { DraftReviewTable } from '../components/DraftReviewTable';

// ─── Styles ────────────────────────────────────────────────────────────────────

const containerStyle: CSSProperties = {
  padding: '24px',
  maxWidth: '900px',
  margin: '0 auto',
};

const sectionStyle: CSSProperties = {
  backgroundColor: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: '10px',
  padding: '20px',
  marginBottom: '20px',
};

const sectionTitleStyle: CSSProperties = {
  margin: '0 0 14px',
  fontSize: '0.95rem',
  fontWeight: 700,
  color: '#111827',
};

const labelStyle: CSSProperties = {
  display: 'block',
  fontSize: '0.8rem',
  fontWeight: 600,
  color: '#374151',
  marginBottom: '4px',
};

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  border: '1px solid #d1d5db',
  borderRadius: '6px',
  fontSize: '0.875rem',
  boxSizing: 'border-box',
  outline: 'none',
};

const primaryBtnStyle: CSSProperties = {
  padding: '10px 20px',
  backgroundColor: '#1d4ed8',
  color: '#ffffff',
  border: 'none',
  borderRadius: '6px',
  cursor: 'pointer',
  fontSize: '0.875rem',
  fontWeight: 700,
};

const secondaryBtnStyle: CSSProperties = {
  padding: '10px 20px',
  backgroundColor: '#16a34a',
  color: '#ffffff',
  border: 'none',
  borderRadius: '6px',
  cursor: 'pointer',
  fontSize: '0.875rem',
  fontWeight: 700,
};

const disabledBtnStyle: CSSProperties = {
  ...primaryBtnStyle,
  backgroundColor: '#9ca3af',
  cursor: 'not-allowed',
  opacity: 0.7,
};

const fieldGroupStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
  gap: '12px',
  marginBottom: '16px',
};

// ─── State shapes ──────────────────────────────────────────────────────────────

interface GitlabFormState {
  username: string;
  project: string;
  token: string;
}

interface JiraFormState {
  base_url: string;
  email: string;
  token: string;
  project_key: string;
  usernames: string; // comma-separated in UI; split to array on submit
}

// ─── File Dropzone ─────────────────────────────────────────────────────────────

interface FileDropzoneProps {
  acceptedTypes: string[];
  file: File | null;
  onFileSelect: (f: File) => void;
}

function FileDropzone({ acceptedTypes, file, onFileSelect }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const acceptAttr = acceptedTypes.length > 0
    ? acceptedTypes.join(',')
    : '.csv,.json,.txt,.md';

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) {
      onFileSelect(dropped);
    }
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleClick = () => {
    inputRef.current?.click();
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    if (picked) {
      onFileSelect(picked);
    }
  };

  const dropzoneStyle: CSSProperties = {
    border: `2px dashed ${isDragOver ? '#1d4ed8' : '#d1d5db'}`,
    borderRadius: '8px',
    padding: '28px 20px',
    textAlign: 'center',
    cursor: 'pointer',
    backgroundColor: isDragOver ? '#eff6ff' : '#fafafa',
    transition: 'border-color 0.15s, background-color 0.15s',
  };

  return (
    <div
      data-testid="file-dropzone"
      style={dropzoneStyle}
      onClick={handleClick}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      role="button"
      tabIndex={0}
      aria-label="File upload area — click or drag a file here"
      onKeyDown={(e) => e.key === 'Enter' && handleClick()}
    >
      <input
        ref={inputRef}
        data-testid="file-input"
        type="file"
        accept={acceptAttr}
        style={{ display: 'none' }}
        onChange={handleInputChange}
      />
      {file != null ? (
        <div>
          <p style={{ margin: '0 0 4px', fontWeight: 700, color: '#111827', fontSize: '0.875rem' }}>
            📎 {file.name}
          </p>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '0.75rem' }}>
            {(file.size / 1024).toFixed(1)} KB — click to change
          </p>
        </div>
      ) : (
        <div>
          <p style={{ margin: '0 0 6px', color: '#374151', fontWeight: 600, fontSize: '0.875rem' }}>
            Drop a file here or click to browse
          </p>
          <p style={{ margin: 0, color: '#9ca3af', fontSize: '0.75rem' }}>
            Accepted: {acceptAttr}
          </p>
        </div>
      )}
    </div>
  );
}

// ─── GitLab form ───────────────────────────────────────────────────────────────

interface GitlabFormProps {
  form: GitlabFormState;
  onChange: (form: GitlabFormState) => void;
}

function GitlabForm({ form, onChange }: GitlabFormProps) {
  const set = (key: keyof GitlabFormState) => (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange({ ...form, [key]: e.target.value });
  };

  return (
    <div>
      <div style={fieldGroupStyle}>
        <div>
          <label style={labelStyle} htmlFor="gitlab-username">
            Username <span style={{ color: '#ef4444' }}>*</span>
          </label>
          <input
            id="gitlab-username"
            data-testid="gitlab-username"
            type="text"
            placeholder="e.g. ada"
            value={form.username}
            onChange={set('username')}
            required
            style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor="gitlab-project">
            Project <span style={{ color: '#9ca3af', fontWeight: 400 }}>(optional)</span>
          </label>
          <input
            id="gitlab-project"
            data-testid="gitlab-project"
            type="text"
            placeholder="e.g. platform/core"
            value={form.project}
            onChange={set('project')}
            style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor="gitlab-token">
            Personal Access Token <span style={{ color: '#9ca3af', fontWeight: 400 }}>(optional)</span>
          </label>
          <input
            id="gitlab-token"
            data-testid="gitlab-token"
            type="password"
            placeholder="glpat-…"
            value={form.token}
            onChange={set('token')}
            style={inputStyle}
            autoComplete="off"
          />
        </div>
      </div>
    </div>
  );
}

// ─── Jira form ─────────────────────────────────────────────────────────────────

interface JiraFormProps {
  form: JiraFormState;
  onChange: (form: JiraFormState) => void;
}

function JiraForm({ form, onChange }: JiraFormProps) {
  const set = (key: keyof JiraFormState) => (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange({ ...form, [key]: e.target.value });
  };

  return (
    <div>
      <div style={fieldGroupStyle}>
        <div>
          <label style={labelStyle} htmlFor="jira-base-url">
            Base URL <span style={{ color: '#ef4444' }}>*</span>
          </label>
          <input
            id="jira-base-url"
            data-testid="jira-base-url"
            type="url"
            placeholder="https://org.atlassian.net"
            value={form.base_url}
            onChange={set('base_url')}
            required
            style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor="jira-email">
            Email <span style={{ color: '#ef4444' }}>*</span>
          </label>
          <input
            id="jira-email"
            data-testid="jira-email"
            type="email"
            placeholder="manager@example.com"
            value={form.email}
            onChange={set('email')}
            required
            style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor="jira-token">
            API Token <span style={{ color: '#ef4444' }}>*</span>
          </label>
          <input
            id="jira-token"
            data-testid="jira-token"
            type="password"
            placeholder="Jira API token"
            value={form.token}
            onChange={set('token')}
            required
            style={inputStyle}
            autoComplete="off"
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor="jira-project-key">
            Project Key <span style={{ color: '#ef4444' }}>*</span>
          </label>
          <input
            id="jira-project-key"
            data-testid="jira-project-key"
            type="text"
            placeholder="e.g. NS"
            value={form.project_key}
            onChange={set('project_key')}
            required
            style={inputStyle}
          />
        </div>
        <div>
          <label style={labelStyle} htmlFor="jira-usernames">
            Usernames <span style={{ color: '#ef4444' }}>*</span>
            <span style={{ color: '#9ca3af', fontWeight: 400 }}> (comma-separated)</span>
          </label>
          <input
            id="jira-usernames"
            data-testid="jira-usernames"
            type="text"
            placeholder="ada, grace, alan"
            value={form.usernames}
            onChange={set('usernames')}
            required
            style={inputStyle}
          />
        </div>
      </div>
    </div>
  );
}

// ─── IngestionSummary banner ───────────────────────────────────────────────────

interface SummaryBannerProps {
  summary: IngestionSummary;
  mode: 'preview' | 'commit';
}

function SummaryBanner({ summary, mode }: SummaryBannerProps) {
  const isPreview = mode === 'preview';
  const bgColor = isPreview ? '#eff6ff' : '#f0fdf4';
  const borderColor = isPreview ? '#93c5fd' : '#86efac';
  const textColor = isPreview ? '#1e40af' : '#15803d';

  return (
    <div
      data-testid="ingestion-summary-banner"
      style={{
        padding: '14px 16px',
        backgroundColor: bgColor,
        border: `1px solid ${borderColor}`,
        borderRadius: '8px',
        marginBottom: '16px',
        fontSize: '0.875rem',
        color: textColor,
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: '6px' }}>
        {isPreview ? '🔍 Preview completed' : '✅ Ingestion committed'}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
        <span>Extracted: <strong>{summary.extracted}</strong></span>
        <span>Enriched: <strong>{summary.enriched}</strong></span>
        <span>Skipped: <strong>{summary.skipped}</strong></span>
        {!isPreview && (
          <span data-testid="ingestion-created-count">
            Created: <strong>{summary.created}</strong>
          </span>
        )}
        <span>LLM: <strong>{summary.provenance.llm}</strong></span>
        <span>Heuristic: <strong>{summary.provenance.heuristic}</strong></span>
      </div>
      {summary.errors.length > 0 && (
        <div style={{ marginTop: '10px' }}>
          <div style={{ fontWeight: 600, marginBottom: '4px' }}>⚠ Warnings / errors:</div>
          <ul style={{ margin: 0, paddingLeft: '18px' }}>
            {summary.errors.map((err, i) => (
              <li key={i} style={{ fontSize: '0.8rem' }}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ─── Main IngestionPage component ──────────────────────────────────────────────

export function IngestionPage() {
  // Connector selection
  const [selectedConnector, setSelectedConnector] = useState<ConnectorInfo | null>(null);

  // File-connector state
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  // GitLab form state
  const [gitlabForm, setGitlabForm] = useState<GitlabFormState>({
    username: '',
    project: '',
    token: '',
  });

  // Jira form state
  const [jiraForm, setJiraForm] = useState<JiraFormState>({
    base_url: '',
    email: '',
    token: '',
    project_key: '',
    usernames: '',
  });

  // Results
  const [previewResult, setPreviewResult] = useState<IngestionSummary | null>(null);
  const [commitResult, setCommitResult] = useState<IngestionSummary | null>(null);

  // Loading / error
  const [previewing, setPreviewing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Connector selection ──
  const handleConnectorSelect = (connector: ConnectorInfo) => {
    setSelectedConnector(connector);
    setSelectedFile(null);
    setPreviewResult(null);
    setCommitResult(null);
    setError(null);
  };

  // ── Build request payload ──
  const buildPayload = (mode: 'preview' | 'commit'): {
    kind: 'file';
    file: File;
    source: 'cv' | 'hr' | 'slack';
    mode: 'preview' | 'commit';
  } | {
    kind: 'gitlab';
    payload: GitlabIngestionRequest;
  } | {
    kind: 'jira';
    payload: JiraIngestionRequest;
  } | null => {
    if (selectedConnector == null) return null;

    if (selectedConnector.kind === 'file') {
      if (selectedFile == null) return null;
      return {
        kind: 'file',
        file: selectedFile,
        source: selectedConnector.source as 'cv' | 'hr' | 'slack',
        mode,
      };
    }

    if (selectedConnector.source === 'gitlab') {
      return {
        kind: 'gitlab',
        payload: {
          username: gitlabForm.username.trim(),
          project: gitlabForm.project.trim() || null,
          token: gitlabForm.token.trim() || null,
          mode,
        },
      };
    }

    if (selectedConnector.source === 'jira') {
      const usernames = jiraForm.usernames
        .split(',')
        .map((u) => u.trim())
        .filter(Boolean);
      return {
        kind: 'jira',
        payload: {
          base_url: jiraForm.base_url.trim() || null,
          email: jiraForm.email.trim() || null,
          token: jiraForm.token.trim() || null,
          project_key: jiraForm.project_key.trim() || null,
          usernames: usernames.length > 0 ? usernames : null,
          mode,
        },
      };
    }

    return null;
  };

  // ── Validate readiness for submit ──
  const canSubmit = (): boolean => {
    if (selectedConnector == null) return false;
    if (selectedConnector.kind === 'file') return selectedFile != null;
    if (selectedConnector.source === 'gitlab') return gitlabForm.username.trim().length > 0;
    if (selectedConnector.source === 'jira') {
      return (
        jiraForm.base_url.trim().length > 0 &&
        jiraForm.email.trim().length > 0 &&
        jiraForm.token.trim().length > 0 &&
        jiraForm.project_key.trim().length > 0 &&
        jiraForm.usernames.trim().length > 0
      );
    }
    return false;
  };

  // ── Execute request ──
  const executeRequest = async (mode: 'preview' | 'commit'): Promise<IngestionSummary | null> => {
    const p = buildPayload(mode);
    if (p == null) return null;

    if (p.kind === 'file') {
      return uploadFile(p.file, p.source, p.mode);
    }
    if (p.kind === 'gitlab') {
      return ingestGitlab(p.payload);
    }
    if (p.kind === 'jira') {
      return ingestJira(p.payload);
    }
    return null;
  };

  // ── Preview handler ──
  const handlePreview = async () => {
    setError(null);
    setPreviewResult(null);
    setCommitResult(null);
    setPreviewing(true);
    try {
      const result = await executeRequest('preview');
      if (result) {
        setPreviewResult(result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed.');
    } finally {
      setPreviewing(false);
    }
  };

  // ── Commit handler ──
  const handleCommit = async () => {
    setError(null);
    setCommitResult(null);
    setCommitting(true);
    try {
      const result = await executeRequest('commit');
      if (result) {
        setCommitResult(result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ingestion commit failed.');
    } finally {
      setCommitting(false);
    }
  };

  // ── Accepted file types ──
  const acceptedTypes: string[] = selectedConnector?.accepted_file_types?.length
    ? selectedConnector.accepted_file_types
    : ['.csv', '.json', '.txt', '.md'];

  // ── Render ──
  return (
    <div data-testid="ingestion-page" style={containerStyle}>
      {/* Page header */}
      <header style={{ marginBottom: '24px' }}>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#111827' }}>
          Data Ingestion
        </h1>
        <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: '0.875rem' }}>
          Import developer profiles from external sources. Preview before committing.
        </p>
      </header>

      {/* Step 1 — Connector picker */}
      <div style={sectionStyle}>
        <h2 style={sectionTitleStyle}>1. Select a connector</h2>
        <ConnectorPicker selected={selectedConnector} onSelect={handleConnectorSelect} />
      </div>

      {/* Step 2 — Source input (only rendered when connector selected) */}
      {selectedConnector !== null && (
        <div style={sectionStyle}>
          <h2 style={sectionTitleStyle}>
            2. Configure source —{' '}
            <span style={{ color: '#1d4ed8' }}>{selectedConnector.display_name ?? selectedConnector.source}</span>
          </h2>

          {selectedConnector.kind === 'file' && (
            <FileDropzone
              acceptedTypes={acceptedTypes}
              file={selectedFile}
              onFileSelect={setSelectedFile}
            />
          )}

          {selectedConnector.source === 'gitlab' && (
            <GitlabForm form={gitlabForm} onChange={setGitlabForm} />
          )}

          {selectedConnector.source === 'jira' && (
            <JiraForm form={jiraForm} onChange={setJiraForm} />
          )}
        </div>
      )}

      {/* Step 3 — Actions */}
      {selectedConnector !== null && (
        <div style={sectionStyle}>
          <h2 style={sectionTitleStyle}>3. Preview &amp; approve</h2>

          {error !== null && (
            <div
              data-testid="ingestion-error"
              style={{
                padding: '10px 14px',
                marginBottom: '14px',
                backgroundColor: '#fef2f2',
                border: '1px solid #fca5a5',
                borderRadius: '6px',
                color: '#b91c1c',
                fontSize: '0.875rem',
              }}
            >
              <strong>Error:</strong> {error}
            </div>
          )}

          {/* Preview results */}
          {previewResult !== null && (
            <>
              <SummaryBanner summary={previewResult} mode="preview" />
              {previewResult.drafts.length > 0 && (
                <div style={{ marginBottom: '16px' }}>
                  <h3 style={{ margin: '0 0 10px', fontSize: '0.875rem', fontWeight: 700, color: '#374151' }}>
                    Draft profiles ({previewResult.drafts.length})
                  </h3>
                  <DraftReviewTable drafts={previewResult.drafts} />
                </div>
              )}
            </>
          )}

          {/* Commit result */}
          {commitResult !== null && (
            <SummaryBanner summary={commitResult} mode="commit" />
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <button
              data-testid="preview-button"
              disabled={!canSubmit() || previewing || committing}
              onClick={() => void handlePreview()}
              style={canSubmit() && !previewing && !committing ? primaryBtnStyle : disabledBtnStyle}
            >
              {previewing ? '⏳ Loading preview…' : '🔍 Preview'}
            </button>

            {previewResult !== null && (
              <button
                data-testid="approve-create-button"
                disabled={!canSubmit() || previewing || committing}
                onClick={() => void handleCommit()}
                style={canSubmit() && !previewing && !committing ? secondaryBtnStyle : disabledBtnStyle}
              >
                {committing ? '⏳ Creating profiles…' : '✅ Approve and Create'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
