/**
 * NEURAL SYNC API Client
 *
 * Conforms to artifacts/api-contracts.json (OpenAPI 3.1).
 * Security model per ADR-002:
 *   - JWT access_token stored in MODULE-LEVEL MEMORY (never localStorage/sessionStorage).
 *   - Refresh token stored in HttpOnly cookie — sent automatically via credentials:'include'.
 *   - 401 responses trigger one silent token-refresh attempt before propagating the error.
 */

// ─── Configuration ─────────────────────────────────────────────────────────────

// VITE_API_BASE_URL can be set in a .env file to override the default.
// vite-env.d.ts references `/// <reference types="vite/client" />` so that
// import.meta.env is fully typed after `npm install`.
const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// ─── In-memory token store (ADR-002: never persisted to Web Storage) ──────────

let _accessToken: string | null = null;

/** Store a new access token in memory. Pass null to clear. */
export function setAccessToken(token: string | null): void {
  _accessToken = token;
}

/** Read the current in-memory access token. */
export function getAccessToken(): string | null {
  return _accessToken;
}

// ─── Schema types — mirrored from api-contracts.json ──────────────────────────

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  user_id: string;
  role: 'developer' | 'manager' | 'admin';
  developer_profile_id: string | null;
}

export interface ComponentScores {
  skill_score: number;
  workstyle_score: number;
  motivation_score: number;
  timezone_score: number;
  growth_score: number;
}

export type ExplanationSource =
  | 'stub_pending'
  | 'claude_cached'
  | 'claude_async'
  | 'stub_permanent';

export interface MatchRecord {
  match_id: string;
  developer_id: string;
  project_id: string;
  project_name: string;
  match_score: number;
  explanation: string;
  explanation_source: ExplanationSource;
  risks: string[];
  growth_potential: string[];
  component_scores: ComponentScores;
  weights_snapshot: Record<string, number>;
  vector_search_degraded: boolean;
  behavioral_data_unavailable: boolean;
  created_at: string;
  explanation_updated_at: string | null;
}

export interface DeveloperMatchesResponse {
  developer_id: string;
  matches: MatchRecord[];
  total: number;
}

/**
 * Operator profile (Identity Layer §2.1). Raw work_style / motivation vectors
 * are intentionally NOT part of this response — they are modeled internally and
 * never exposed via the API.
 */
export interface DeveloperProfile {
  id: string;
  skills: string[];
  experience_years: number;
  preferred_stack: string[];
  timezone: string;
  availability_hours: number;
  career_goals: string[];
  project_history: unknown[];
  is_self_reported: boolean;
  embedding_status: string;
  created_at: string;
  updated_at: string;
}

export type RiskBadgeLevel = 'low' | 'medium' | 'high';

/** Per ADR-002 / AC8: raw behavioral vectors are never in this response. */
export interface RiskResponse {
  developer_id: string;
  burnout_risk_score: number;
  bench_risk_score: number;
  burnout_risk_badge: RiskBadgeLevel;
  bench_risk_badge: RiskBadgeLevel;
  computed_at: string;
}

/** Per AC8: contains only risk scores and badge states — no behavioral vectors. */
export interface TeamRiskMember {
  developer_id: string;
  display_name: string;
  burnout_risk_score: number;
  bench_risk_score: number;
  burnout_risk_badge: RiskBadgeLevel;
  bench_risk_badge: RiskBadgeLevel;
  // Task04 §1 third prediction — null when the developer has no match against an
  // assigned project yet (behavioral fit unknown). Never exposes raw vectors (AC8).
  team_mismatch_probability?: number | null;
  team_mismatch_badge?: RiskBadgeLevel | null;
}

export interface TeamRiskDistribution {
  burnout_high_count: number;
  burnout_medium_count: number;
  burnout_low_count: number;
  bench_high_count: number;
  bench_medium_count: number;
  bench_low_count: number;
}

/** GET /teams/{team_id}/risk-summary — manager dashboard payload. */
export interface TeamRiskSummary {
  team_id: string;
  member_count: number;
  members: TeamRiskMember[];
  risk_distribution: TeamRiskDistribution;
  computed_at: string;
}

export interface ExplanationResponse {
  match_id: string;
  explanation: string;
  explanation_source: ExplanationSource;
  explanation_updated_at: string | null;
}

export interface FeedbackRequest {
  developer_id: string;
  match_id: string;
  accepted: boolean;
  comment?: string | null;
}

export interface FeedbackResponse {
  id: string;
  developer_id: string;
  match_id: string;
  accepted: boolean;
  comment: string | null;
  feedback_timestamp: string;
}

export interface WeightConfig {
  w1: number;
  w2: number;
  w3: number;
  w4: number;
  w5: number;
  version: number;
}

export interface SuggestedProjectMove {
  project_id: string;
  project_name: string;
  match_score: number;
  component_scores: Record<string, number>;
  action_type: string;
  rationale: string;
  projected_burnout_after_move: number;
}

export interface ReallocationSuggestion {
  developer_id: string;
  trigger: 'burnout' | 'bench' | 'none';
  current_burnout_score: number;
  current_bench_score: number;
  current_burnout_badge: RiskBadgeLevel;
  current_bench_badge: RiskBadgeLevel;
  suggestion: SuggestedProjectMove | null;
}

export interface ApiError {
  error_code: string;
  message: string;
  request_id: string;
}

// ─── Error class ───────────────────────────────────────────────────────────────

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ApiError,
  ) {
    super(body.message);
    this.name = 'ApiClientError';
  }
}

// ─── Core HTTP helper ──────────────────────────────────────────────────────────

/**
 * Executes a typed fetch against the NEURAL SYNC REST API.
 * Automatically attaches the in-memory Bearer token and the HttpOnly
 * refresh-token cookie. On HTTP 401, silently refreshes the token and
 * retries once before throwing.
 */
async function request<T>(
  path: string,
  options: RequestInit = {},
  _retry = true,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  // Merge any caller-supplied headers
  if (options.headers) {
    const supplied = options.headers as Record<string, string>;
    Object.keys(supplied).forEach((k) => {
      headers[k] = supplied[k];
    });
  }

  if (_accessToken) {
    headers['Authorization'] = `Bearer ${_accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: 'include', // sends HttpOnly refresh_token cookie automatically
    headers,
  });

  // Silent refresh on 401 (one attempt only to prevent infinite loops)
  if (response.status === 401 && _retry) {
    try {
      await _doRefresh();
      return request<T>(path, options, false);
    } catch {
      _accessToken = null;
      throw new ApiClientError(401, {
        error_code: 'UNAUTHORIZED',
        message: 'Session expired. Please sign in again.',
        request_id: '',
      });
    }
  }

  if (!response.ok) {
    let errorBody: ApiError;
    try {
      errorBody = (await response.json()) as ApiError;
    } catch {
      errorBody = {
        error_code: 'UNKNOWN_ERROR',
        message: `HTTP ${response.status} ${response.statusText}`,
        request_id: '',
      };
    }
    throw new ApiClientError(response.status, errorBody);
  }

  // HTTP 204 No Content — return undefined cast to T
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  return response.json() as Promise<T>;
}

// ─── Auth endpoints ────────────────────────────────────────────────────────────

/**
 * POST /auth/login
 * Stores the returned access_token in memory; refresh_token arrives as an
 * HttpOnly cookie and is handled transparently by the browser.
 */
export async function login(credentials: LoginRequest): Promise<LoginResponse> {
  const data = await request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(credentials),
  }, false);
  setAccessToken(data.access_token);
  return data;
}

/**
 * POST /auth/logout
 * Clears the HttpOnly refresh_token cookie server-side.
 */
export async function logout(): Promise<void> {
  await request<void>('/auth/logout', { method: 'POST' }, false);
}

/**
 * POST /auth/refresh
 * Uses the HttpOnly refresh_token cookie. Rotates the cookie and stores
 * the new access_token in memory.
 */
export async function refreshToken(): Promise<LoginResponse> {
  return _doRefresh();
}

async function _doRefresh(): Promise<LoginResponse> {
  const data = await request<LoginResponse>('/auth/refresh', {
    method: 'POST',
  }, false);
  setAccessToken(data.access_token);
  return data;
}

// ─── Developer matches (Developer Dashboard — AC7) ────────────────────────────

/**
 * GET /developers/{developer_id}/matches
 * Returns top-K MatchRecord objects ranked by match_score descending.
 * Each record contains match_score (float 0-1), explanation (≥50 chars),
 * risks[], and growth_potential[].
 */
export async function getDeveloperMatches(
  developerId: string,
  topK = 10,
  minScore?: number,
): Promise<DeveloperMatchesResponse> {
  const params = new URLSearchParams({ top_k: String(topK) });
  if (minScore !== undefined) {
    params.set('min_score', String(minScore));
  }
  return request<DeveloperMatchesResponse>(
    `/developers/${developerId}/matches?${params.toString()}`,
  );
}

// ─── Developer roster (WS-B5) ───────────────────────────────────────────────────

export interface DeveloperListItem {
  developer_id: string;
  display_name: string | null;
  skills: string[];
  experience_years: number;
  timezone: string;
  availability_hours: number;
  embedding_status: string;
  burnout_risk_badge: RiskBadgeLevel | null;
  bench_risk_badge: RiskBadgeLevel | null;
}

export interface DeveloperListResponse {
  items: DeveloperListItem[];
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
}

export interface RosterQuery {
  limit?: number;
  offset?: number;
  skill?: string;
  timezone?: string;
  risk_badge?: string;
  search?: string;
}

/** GET /developers — paginated, filterable roster (manager/admin). */
export async function listDevelopers(q: RosterQuery = {}): Promise<DeveloperListResponse> {
  const params = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v).length > 0) params.set(k, String(v));
  });
  const qs = params.toString();
  return request<DeveloperListResponse>(`/developers${qs ? `?${qs}` : ''}`);
}

// ─── Explanation polling ───────────────────────────────────────────────────────

/**
 * GET /matches/{match_id}/explanation
 * Polls until explanation_source transitions away from 'stub_pending'.
 * Frontend calls this every ~3 seconds while explanation_source === 'stub_pending'.
 */
export async function getMatchExplanation(matchId: string): Promise<ExplanationResponse> {
  return request<ExplanationResponse>(`/matches/${matchId}/explanation`);
}

// ─── Feedback (AC10) ──────────────────────────────────────────────────────────

/**
 * POST /matches/feedback
 * developer_id in the body must match the authenticated JWT sub claim.
 */
export async function submitFeedback(payload: FeedbackRequest): Promise<FeedbackResponse> {
  return request<FeedbackResponse>('/matches/feedback', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ─── Risk — individual developer ──────────────────────────────────────────────

/**
 * GET /developers/{developer_id}/risk
 * Returns burnout/bench scores and badge levels.
 * Raw behavioral vectors are NEVER included per AC8.
 */
export async function getDeveloperRisk(developerId: string): Promise<RiskResponse> {
  return request<RiskResponse>(`/developers/${developerId}/risk`);
}

// ─── Operator profile (Identity Layer §2.1) ───────────────────────────────────

/** GET /developers/{developer_id} — the authenticated developer's own profile. */
export async function getDeveloperProfile(developerId: string): Promise<DeveloperProfile> {
  return request<DeveloperProfile>(`/developers/${developerId}`);
}

// ─── Weight config (Admin tab — manager only) ─────────────────────────────────

export async function getWeights(): Promise<WeightConfig> {
  return request<WeightConfig>('/config/weights');
}

export async function updateWeights(
  weights: Omit<WeightConfig, 'version'>,
): Promise<WeightConfig> {
  return request<WeightConfig>('/config/weights', {
    method: 'PUT',
    body: JSON.stringify(weights),
  });
}

// ─── Risk — team summary (Manager Dashboard — AC8) ────────────────────────────

/**
 * GET /teams/{team_id}/risk-summary
 * Returns per-member risk badges and distribution counts.
 * Raw behavioral vectors, work_style arrays, and motivation scalars are
 * NEVER included in this response per architecture/AC8.
 */
export async function getTeamRiskSummary(teamId: string): Promise<TeamRiskSummary> {
  return request<TeamRiskSummary>(`/teams/${teamId}/risk-summary`);
}

export async function getReallocationSuggestion(developerId: string): Promise<ReallocationSuggestion> {
  return request<ReallocationSuggestion>(`/developers/${developerId}/reallocation-suggestion`);
}

// ─── Projects (Admin View — Project Genome §2.2) ──────────────────────────────

/** ProjectProfile as returned by the API (no vectors). */
export interface ProjectProfile {
  id: string;
  name: string;
  required_skills: string[];
  team_structure: unknown; // string or object per spec
  workload_intensity: number;
  innovation_level: number;
  timezone_overlap_required: string;
  duration_weeks: number;
  growth_opportunities: string[];
  created_at: string;
  updated_at: string;
}

/** Payload for creating a project (POST /projects). */
export interface ProjectCreate {
  name?: string;
  required_skills: string[];
  team_structure: unknown;
  workload_intensity: number;
  innovation_level: number;
  timezone_overlap_required: string;
  duration_weeks: number;
  growth_opportunities: string[];
}

/** GET /projects — list project profiles (manager/admin). */
export async function listProjects(limit = 100, offset = 0): Promise<ProjectProfile[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<ProjectProfile[]>(`/projects?${params.toString()}`);
}

/** POST /projects — create a project profile (manager/admin). */
export async function createProject(payload: ProjectCreate): Promise<ProjectProfile> {
  return request<ProjectProfile>('/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ─── Ingestion types (AC26, AC29) ─────────────────────────────────────────────

/** ConnectorInfo descriptor returned by GET /ingestion/connectors. */
export interface ConnectorInfo {
  source: 'gitlab' | 'hr' | 'slack' | 'cv' | 'jira';
  display_name: string;
  kind: 'file' | 'network';
  availability: 'live' | 'credential-gated';
  description?: string;
  required_credentials?: string[];
  accepted_file_types?: string[];
}

export interface IngestionProvenance {
  llm: number;
  heuristic: number;
}

export interface IngestionDraft {
  external_id: string | null;
  display_name: string | null;
  email: string | null;
  source: string;
  skills: string[];
  cv_text: string | null;
  git_log_text: string | null;
  slack_text: string | null;
  timezone: string | null;
  availability_hours: number | null;
  experience_years: number | null;
  provenance: 'llm' | 'heuristic' | null;
}

export interface IngestionSummary {
  extracted: number;
  enriched: number;
  skipped: number;
  created: number;
  provenance: IngestionProvenance;
  errors: string[];
  drafts: IngestionDraft[];
}

export interface GitlabIngestionRequest {
  username: string;
  project?: string | null;
  base_url?: string | null;
  token?: string | null;
  mode: 'preview' | 'commit';
}

export interface JiraIngestionRequest {
  base_url?: string | null;
  email?: string | null;
  token?: string | null;
  project_key?: string | null;
  usernames?: string[] | null;
  mode: 'preview' | 'commit';
}

// ─── Ingestion API functions ───────────────────────────────────────────────────

/** GET /ingestion/connectors — list available source connectors (manager only). */
export async function listIngestionConnectors(): Promise<ConnectorInfo[]> {
  return request<ConnectorInfo[]>('/ingestion/connectors');
}

/**
 * POST /ingestion/file — multipart upload for file-kind connectors.
 * Uses a dedicated fetch call so the browser can set multipart/form-data
 * Content-Type with the correct boundary (not overridden by the JSON client).
 *
 * Added via in-place merge; existing client functions are not altered (AC26).
 */
export async function uploadFile(
  file: File,
  source: 'cv' | 'hr' | 'slack',
  mode: 'preview' | 'commit',
): Promise<IngestionSummary> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('source', source);
  formData.append('mode', mode);

  // Do NOT set Content-Type — the browser sets it with the multipart boundary.
  const headers: Record<string, string> = {};
  if (_accessToken) {
    headers['Authorization'] = `Bearer ${_accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/ingestion/file`, {
    method: 'POST',
    credentials: 'include',
    headers,
    body: formData,
  });

  if (!response.ok) {
    let errorBody: ApiError;
    try {
      errorBody = (await response.json()) as ApiError;
    } catch {
      errorBody = {
        error_code: 'UNKNOWN_ERROR',
        message: `HTTP ${response.status} ${response.statusText}`,
        request_id: '',
      };
    }
    throw new ApiClientError(response.status, errorBody);
  }

  return response.json() as Promise<IngestionSummary>;
}

/** POST /ingestion/gitlab — ingest from GitLab activity (manager only). */
export async function ingestGitlab(payload: GitlabIngestionRequest): Promise<IngestionSummary> {
  return request<IngestionSummary>('/ingestion/gitlab', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** POST /ingestion/jira — ingest from Jira activity (manager only, credential-gated). */
export async function ingestJira(payload: JiraIngestionRequest): Promise<IngestionSummary> {
  return request<IngestionSummary>('/ingestion/jira', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
