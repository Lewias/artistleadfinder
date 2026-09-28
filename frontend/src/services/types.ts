export interface LeadSource {
  id: number;
  source_provider: string;
  source_type: string;
  source_value: string;
  search_job_id: number;
}
export interface Lead {
  unknown_fields?: string[];
  id: number;
  username: string;
  platform: string;
  display_name: string;
  bio: string;
  profile_url: string;
  avatar_url: string;
  external_url: string;
  followers: number;
  following: number;
  is_private: boolean;
  is_verified: boolean;
  artist_probability: number;
  primary_genre: string | null;
  genres: string[];
  lead_score: number;
  status: string;
  last_activity_at: string | null;
  created_at: string;
  sources: LeadSource[];
}
export interface LeadDetail extends Lead {
  scout?: {
    explanation: string;
    services: Record<string, { score: number; reasons: { text: string }[] }>;
  } | null;
  analysis: {
    signals: string[];
    confidence: number;
    extracted_signals?: {
      browser_capture?: {
        bio_method?: string;
        captured_at: string;
        unknown_fields: string[];
        description: string;
        header: string;
      };
    };
  } | null;
  breakdown: { rule: string; reason: string; points: number }[];
}
export interface SearchConfig {
  name: string;
  seed_accounts: string[];
  keywords: string[];
  hashtags: string[];
  genres: string[];
  min_followers: number;
  max_followers: number;
  activity_days: number;
  minimum_score: number;
  target_leads: number;
}
export interface SearchJob extends SearchConfig {
  id: number;
  status: string;
  stage: string;
  candidates_found: number;
  profiles_analyzed: number;
  artists_detected: number;
  qualified_leads: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  errors: { message: string; provider?: string }[];
}
export interface DashboardData {
  total: number;
  qualified: number;
  today: number;
  active: number;
  genres: { name: string; count: number }[];
  distribution: number[];
  jobs: SearchJob[];
  leads: Lead[];
}
export interface LeadQuery {
  page: number;
  page_size: number;
  search: string;
  genre: string;
  status: string;
  source: string;
  minimum_score: number;
  min_followers: number;
  max_followers: number;
  activity_days: number | null;
  sort: string;
  descending: boolean;
  job_id?: number;
}
export interface SettingsData {
  min_followers: number;
  max_followers: number;
  activity_days: number;
  minimum_score: number;
  target_leads: number;
  enabled_providers: string[];
  weights: Record<string, number>;
  page_delay_min: number;
  page_delay_max: number;
  profiles_per_hour: number;
  profiles_per_run: number;
  rate_limit_pause_minutes: number;
  scout_methods: ScoutMethod[];
  scout_profile_type: 'artists' | 'artists_producers' | 'everyone';
  scout_min_followers: number;
  scout_max_followers: number;
  scout_only_contacts: boolean;
  scout_skip_processed: boolean;
  scout_skip_recent_sources: boolean;
  scout_source_cooldown_hours: number;
  scout_sources_per_run: number;
  scout_follow_page_size: number;
  scout_follow_delay_seconds: number;
  scout_follow_max: number;
  scout_ai_mode: 'off' | 'uncertain' | 'always';
  scout_ai_model: string;
}
export type ScoutMethod = 'posts' | 'comments' | 'tagged' | 'stories' | 'followers' | 'following';
export interface ScoutStats {
  discovered: number;
  analyzed: number;
  leads: number;
  skipped: number;
  errors: number;
  current_source: string | null;
  current_profile: string | null;
  sources: string[];
  sources_done: string[];
}
export interface ScoutSourceRow {
  url: string;
  username: string;
  enabled: boolean;
  last_scanned_at: string | null;
  status: string;
  leads_found: number;
}
export interface ScoutEvent {
  id: number;
  job_id: number;
  type: string;
  payload: {
    username?: string;
    source?: string;
    method?: string;
    reason?: string;
    category?: string;
    confidence?: number;
    log?: string;
    [key: string]: unknown;
  };
  created_at: string;
}
export interface ProviderHealth {
  provider: string;
  status: string;
  last_request_at?: string;
  last_success_at?: string;
  last_error: string | null;
}
export interface BrowserProxy {
  scheme: 'http' | 'socks5';
  host: string;
  port: number;
  username?: string | null;
  has_password?: boolean;
}
export interface BrowserProfile {
  id: string;
  name: string;
  cookie_count: number;
  proxy: BrowserProxy | null;
}
export interface CaptureQueue {
  id: number;
  scout?: boolean;
  kind?: string;
  status: string;
  stage: string;
  cursor: number;
  total: number;
  url: string | null;
  error: string | null;
  candidates?: number;
  notices?: string[];
  wait_seconds?: number;
  wait_reason?: string | null;
  profile_id?: string;
  found?: number;
  backlog?: number;
  stats?: ScoutStats;
}
export interface ScoutAccountRow {
  profile: BrowserProfile;
  target: number;
  found: number;
  run: CaptureQueue | null;
}
