/**
 * App — top-level component wiring login + role-based dashboard routing.
 *
 * Auth flow (ADR-002):
 *   1. User submits username + password → POST /auth/login.
 *   2. access_token is stored in MODULE-LEVEL MEMORY by the API client
 *      (never localStorage/sessionStorage).
 *   3. refresh_token arrives as an HttpOnly cookie; the API client sends it
 *      automatically on every subsequent request via credentials:'include'.
 *   4. On logout, the in-memory token is cleared.
 *
 * Routing:
 *   role === 'developer'  →  DeveloperDashboard (AC7)
 *   role === 'manager'    →  ManagerDashboard   (AC8)
 */

import { useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import type { LoginResponse } from './api/client';
import { login, logout, setAccessToken } from './api/client';
import { DeveloperDashboard } from './pages/DeveloperDashboard';
import { IngestionPage } from './pages/IngestionPage';
import { ManagerDashboard } from './pages/ManagerDashboard';
import { WeightConfigPage } from './pages/WeightConfigPage';
import { ProfilePage } from './pages/ProfilePage';

type ManagerTab = 'risk' | 'weights' | 'profile' | 'ingestion';
type DeveloperTab = 'recommendations' | 'profile';

// ─── Styles ────────────────────────────────────────────────────────────────────

const inputStyle: CSSProperties = {
  padding: '9px 12px',
  border: '1px solid #d1d5db',
  borderRadius: '6px',
  fontSize: '0.875rem',
  width: '100%',
  outline: 'none',
  boxSizing: 'border-box',
};

const primaryBtn: CSSProperties = {
  padding: '10px 16px',
  backgroundColor: '#1d4ed8',
  color: '#ffffff',
  border: 'none',
  borderRadius: '6px',
  cursor: 'pointer',
  fontSize: '0.875rem',
  fontWeight: 700,
  width: '100%',
};

// ─── Login form ────────────────────────────────────────────────────────────────

interface LoginFormProps {
  onSuccess: (resp: LoginResponse) => void;
}

function LoginForm({ onSuccess }: LoginFormProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setBusy(true);
    try {
      const resp = await login({ username, password });
      onSuccess(resp);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Login failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#f3f4f6',
      }}
    >
      <div
        style={{
          backgroundColor: '#ffffff',
          border: '1px solid #e5e7eb',
          borderRadius: '12px',
          padding: '36px 32px',
          width: '100%',
          maxWidth: '380px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
        }}
      >
        <h1
          style={{
            margin: '0 0 6px',
            fontSize: '1.3rem',
            fontWeight: 800,
            color: '#111827',
            textAlign: 'center',
          }}
        >
          NEURAL SYNC
        </h1>
        <p
          style={{
            margin: '0 0 24px',
            fontSize: '0.8rem',
            color: '#6b7280',
            textAlign: 'center',
          }}
        >
          AI-driven developer – project matching
        </p>

        <form
          onSubmit={(e) => void handleSubmit(e)}
          style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}
        >
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoComplete="username"
            style={inputStyle}
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            style={inputStyle}
          />
          {errorMsg !== null && (
            <p style={{ margin: 0, color: '#ef4444', fontSize: '0.8rem' }}>{errorMsg}</p>
          )}
          <button type="submit" disabled={busy} style={{ ...primaryBtn, opacity: busy ? 0.7 : 1 }}>
            {busy ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ─── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [session, setSession] = useState<LoginResponse | null>(null);
  const [teamId] = useState<string>('00000000-0000-0000-0000-000000000001');
  const [managerTab, setManagerTab] = useState<ManagerTab>('risk');
  const [developerTab, setDeveloperTab] = useState<DeveloperTab>('recommendations');

  const handleLoginSuccess = (resp: LoginResponse) => {
    setSession(resp);
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // best-effort: clear client state regardless
    }
    setAccessToken(null);
    setSession(null);
    setManagerTab('risk');
    setDeveloperTab('recommendations');
  };

  // ── Unauthenticated → show login form ──
  if (session === null) {
    return <LoginForm onSuccess={handleLoginSuccess} />;
  }

  // ── Developer role → tabbed view: Recommendations | Profile ──
  const devTabBtn = (active: boolean): CSSProperties => ({
    padding: '4px 14px',
    backgroundColor: active ? '#ffffff' : 'transparent',
    color: active ? '#1d4ed8' : '#bfdbfe',
    border: active ? '1px solid #93c5fd' : '1px solid transparent',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.8rem',
    fontWeight: 600,
  });

  if (session.role === 'developer') {
    const devId = session.developer_profile_id ?? session.user_id;
    return (
      <div style={{ minHeight: '100vh', backgroundColor: '#f9fafb' }}>
        <nav
          style={{
            backgroundColor: '#1d4ed8',
            padding: '10px 24px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            boxShadow: '0 2px 4px rgba(0,0,0,0.15)',
          }}
        >
          <span style={{ color: '#ffffff', fontWeight: 800, fontSize: '0.95rem', letterSpacing: '0.05em' }}>
            NEURAL SYNC
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button onClick={() => setDeveloperTab('recommendations')} style={devTabBtn(developerTab === 'recommendations')}>
              Recommendations
            </button>
            <button onClick={() => setDeveloperTab('profile')} style={devTabBtn(developerTab === 'profile')}>
              My Profile
            </button>
            <span style={{ color: '#93c5fd', fontSize: '0.8rem', fontWeight: 600, marginLeft: '8px' }}>
              👤 Developer
            </span>
            <button
              onClick={() => void handleLogout()}
              style={{ color: '#bfdbfe', background: 'none', border: '1px solid #93c5fd', borderRadius: '6px', padding: '4px 12px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 }}
            >
              Sign Out
            </button>
          </div>
        </nav>

        {developerTab === 'recommendations' ? (
          <DeveloperDashboard developerId={devId} />
        ) : (
          <ProfilePage role="developer" developerId={devId} userId={session.user_id} />
        )}
      </div>
    );
  }

  // ── Manager role → tabbed view: Team Risk | Weight Config ──
  const tabBtnStyle = (active: boolean): CSSProperties => ({
    padding: '4px 14px',
    backgroundColor: active ? '#ffffff' : 'transparent',
    color: active ? '#1d4ed8' : '#bfdbfe',
    border: active ? '1px solid #93c5fd' : '1px solid transparent',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.8rem',
    fontWeight: 600,
  });

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#f9fafb' }}>
      {/* Nav with inline tabs */}
      <nav
        style={{
          backgroundColor: '#1d4ed8',
          padding: '10px 24px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          boxShadow: '0 2px 4px rgba(0,0,0,0.15)',
        }}
      >
        <span style={{ color: '#ffffff', fontWeight: 800, fontSize: '0.95rem', letterSpacing: '0.05em' }}>
          NEURAL SYNC
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button onClick={() => setManagerTab('risk')} style={tabBtnStyle(managerTab === 'risk')}>
            Team Risk
          </button>
          <button onClick={() => setManagerTab('weights')} style={tabBtnStyle(managerTab === 'weights')}>
            Weight Config
          </button>
          <button
            data-testid="ingestion-tab-button"
            onClick={() => setManagerTab('ingestion')}
            style={tabBtnStyle(managerTab === 'ingestion')}
          >
            Ingestion
          </button>
          <button onClick={() => setManagerTab('profile')} style={tabBtnStyle(managerTab === 'profile')}>
            My Profile
          </button>
          <span style={{ color: '#93c5fd', fontSize: '0.8rem', fontWeight: 600, marginLeft: '8px' }}>
            ⚙ Manager
          </span>
          <button
            onClick={() => void handleLogout()}
            style={{
              color: '#bfdbfe',
              background: 'none',
              border: '1px solid #93c5fd',
              borderRadius: '6px',
              padding: '4px 12px',
              cursor: 'pointer',
              fontSize: '0.8rem',
              fontWeight: 600,
            }}
          >
            Sign Out
          </button>
        </div>
      </nav>

      {managerTab === 'risk' && <ManagerDashboard teamId={teamId} />}
      {managerTab === 'weights' && <WeightConfigPage />}
      {managerTab === 'ingestion' && <IngestionPage />}
      {managerTab === 'profile' && <ProfilePage role="manager" userId={session.user_id} />}
    </div>
  );
}
