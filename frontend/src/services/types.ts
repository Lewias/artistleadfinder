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
        discovery?: { provider: string; query: string };
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
}
