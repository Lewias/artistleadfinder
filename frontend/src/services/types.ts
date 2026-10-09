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
  do_not_contact?: boolean;
  last_contacted_at?: string | null;
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
  /** Lead Scout columns; null for leads from other sources. */
  scout_profile?: LeadScoutSummary | null;
}
export type ProfileCategory = 'artist' | 'producer' | 'media' | 'other';
export interface LeadScoutSummary {
  profile_type: ProfileCategory;
  confidence: number;
  emails: string[];
  phones: string[];
  source_username: string;
  discovery_method: string;
  last_seen_at: string | null;
}
export interface LeadFoundVia {
  source_username: string;
  discovery_method: string;
  origin_url: string | null;
  first_seen_at: string;
  last_seen_at: string;
  times_seen: number;
}
export interface LeadDetail extends Lead {
  outreach?: LeadOutreach;
  scout?: {
    explanation: string;
    services: Record<string, { score: number; reasons: { text: string }[] }>;
  } | null;
  classification?: {
    category: ProfileCategory;
    confidence: number;
    decided_by: 'local' | 'ai' | 'local+ai';
    reasons: string[];
    ai_model: string | null;
    local_category?: ProfileCategory | null;
    local_confidence?: number | null;
    ai_category?: ProfileCategory | null;
    ai_confidence?: number | null;
  } | null;
  found_via?: LeadFoundVia[];
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
  scout_use_followers_range: boolean;
  scout_min_followers: number;
  scout_max_followers: number;
  scout_only_contacts: boolean;
  scout_allow_unknown_followers: boolean;
  scout_min_confidence: number;
  scout_lead_status: 'new' | 'reviewed' | 'qualified';
  scout_add_to_outreach: boolean;
  scout_skip_processed: boolean;
  scout_skip_recent_sources: boolean;
  scout_rotate_sources: boolean;
  scout_source_cooldown_hours: number;
  scout_sources_per_run: number;
  scout_follow_page_size: number;
  scout_follow_delay_seconds: number;
  scout_followers_max: number;
  scout_following_max: number;
  scout_ai_mode: 'off' | 'uncertain' | 'always';
  scout_ai_model: string;
  scout_ai_timeout_seconds: number;
  scout_ai_concurrency: number;
  scout_ai_min_confidence: number;
  scout_max_posts_per_source: number;
  scout_max_scroll_rounds: number;
  scout_scroll_delay_ms: number;
  scout_max_no_progress_rounds: number;
  scout_max_retries: number;
  scout_max_item_failures: number;
  scout_debug: boolean;
  outreach_skip_previously_contacted: boolean;
  outreach_send_interval_seconds: number;
  outreach_daily_limit_per_sender: number;
  outreach_max_attempts: number;
  outreach_rate_limit_pause_minutes: number;
  inbox_region: string;
  inbox_max_threads: number;
  inbox_delay_min_seconds: number;
  inbox_delay_max_seconds: number;
  scout_ignore_usernames: string[];
  scout_profile_cache_hours: number;
  scout_recent_captions: number;
}
export interface DiscoveryMetrics {
  itemsSeen: number;
  itemsProcessed: number;
  candidatesFound: number;
  duplicatesSkipped: number;
  alreadyProcessed: number;
  failures: number;
}
export type ScoutMethod = 'profiles' | 'posts' | 'tagged' | 'followers' | 'following';
export interface ScoutStats {
  discovered: number;
  resolved?: number;
  classified?: number;
  analyzed: number;
  leads: number;
  leads_updated?: number;
  skipped: number;
  errors: number;
  /** Skips per LeadSkipReason in this run. */
  skips?: Record<string, number>;
  current_source: string | null;
  current_profile: string | null;
  sources: string[];
  sources_done: string[];
  providers?: Record<string, Record<string, DiscoveryMetrics>>;
}
export interface ScoutSourceRow {
  url: string;
  username: string;
  enabled: boolean;
  last_scanned_at: string | null;
  status: string;
  leads_found: number;
  candidates_found?: number;
  profiles_resolved?: number;
  profiles_skipped?: number;
  errors_count?: number;
}
export type ScoutEventType =
  | 'scout:run-started'
  | 'scout:source-started'
  | 'scout:source-completed'
  | 'scout:candidate-found'
  | 'scout:profile-resolving'
  | 'scout:profile-resolved'
  | 'scout:classification-started'
  | 'scout:classification-completed'
  | 'scout:profile-skipped'
  | 'scout:lead-created'
  | 'scout:lead-updated'
  | 'scout:error'
  | 'scout:paused'
  | 'scout:resumed'
  | 'scout:cancelled'
  | 'scout:completed'
  | 'scout:discovery-page';
export interface ScoutEvent {
  id: number;
  job_id: number;
  type: ScoutEventType;
  payload: {
    run_id?: number;
    lead_id?: number;
    username?: string;
    source?: string;
    method?: string;
    reason?: string;
    details?: string | null;
    kind?: 'profile' | 'source' | 'fatal' | 'rate_limit';
    category?: string | null;
    confidence?: number | null;
    change?: string;
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

// ---------- Outreach ----------

export type CampaignStatus =
  'draft' | 'scheduled' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed';
export type RecipientStatus = 'pending' | 'queued' | 'sending' | 'sent' | 'skipped' | 'failed' | 'cancelled';
export type SenderStatus = 'active' | 'paused' | 'auth_required' | 'checkpoint' | 'rate_limited' | 'disabled';

export interface OutreachTemplate {
  id: number;
  name: string;
  body: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
export interface FollowUpSequence {
  id: number;
  name: string;
  steps: { delay_days: number; template_id: number }[];
  enabled: boolean;
}
export interface RenderedTemplate {
  text: string;
  valid: boolean;
  errors: string[];
  fallbacks: string[];
  length: number;
}
export interface AudienceLead {
  id: number;
  username: string;
  display_name: string;
  followers: number;
  status: string;
  do_not_contact: boolean;
  contacted: boolean;
  created_at: string;
  profile_type: string | null;
  confidence: number | null;
  email: string | null;
  phone: string | null;
  source_username: string | null;
  discovery_method: string | null;
}
export interface AudienceQuery {
  search: string;
  profile_types: string[];
  min_followers: number;
  max_followers: number | null;
  has_email: boolean;
  has_phone: boolean;
  statuses: string[];
  source_username: string;
  discovery_method: string;
  min_confidence: number;
  created_from: string | null;
  created_to: string | null;
  include_dnc: boolean;
  include_contacted: boolean;
  page: number;
  page_size: number;
}
export interface OutreachSender {
  id: string;
  name: string;
  has_session: boolean;
  open: boolean;
  status: SenderStatus;
  reason: string | null;
  until: string | null;
  last_sent_at: string | null;
  sent_24h: number;
  daily_limit: number;
}
/** «Найти и написать»: the parser run of one account, then «Рассылка» to its new leads. */
export interface AutopilotState {
  status: 'idle' | 'starting' | 'scouting' | 'sending' | 'done' | 'failed' | 'stopped' | 'cancelled';
  profile_id: string;
  account: string;
  target: number;
  scout_found: number;
  found?: number;
  sent: number;
  total: number;
  message: string;
}
/** «Ответы»: one reading of an account's outreach threads. */
export interface InboxScan {
  id: number;
  sender: string;
  sender_name: string;
  status: 'running' | 'done' | 'stopped';
  days: number;
  total: number;
  done: number;
  replied: number;
  found: number;
  errors: number;
  reason: string;
  /** Why the next thread waits (closed window, parser running); empty while reading. */
  waiting: string;
  started_at: string;
  finished_at: string | null;
}
export interface InboxFinding {
  id: number;
  username: string;
  kind: 'phone' | 'email';
  value: string;
  raw: string;
  snippet: string;
  /** The country code came from the default region. */
  guessed: boolean;
  sender: string;
  found_at: string;
}
export interface InboxState {
  scan: InboxScan | null;
  findings: InboxFinding[];
  counts: { new: number; added: number; hidden: number };
}
export interface CampaignPreview {
  total: number;
  eligible: number;
  skipped: Record<string, number>;
  invalid_messages: number;
  accounts: number;
  active_accounts: number;
  estimated_queued: number;
  examples: {
    lead_id: number;
    username: string;
    display_name: string;
    sender_id: string | null;
    sender_name: string;
    message: string;
    fallbacks: string[];
  }[];
}
export interface OutreachCampaign {
  id: number;
  name: string;
  status: CampaignStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  scheduled_at: string | null;
  template_id: number;
  followup_sequence_id: number | null;
  sender_strategy: 'single' | 'round_robin';
  sender_ids: string[];
  total_recipients: number;
  queued_count: number;
  sent_count: number;
  skipped_count: number;
  failed_count: number;
  replied_count: number;
}
export interface CampaignDetail extends OutreachCampaign {
  template: OutreachTemplate | null;
  sequence: FollowUpSequence | null;
  senders: Pick<OutreachSender, 'id' | 'name' | 'status' | 'reason' | 'until' | 'sent_24h' | 'daily_limit'>[];
  reasons: Record<string, number>;
}
export interface CampaignRecipient {
  id: number;
  campaign_id: number;
  lead_id: number;
  username: string;
  display_name: string;
  followers: number;
  profile_type: string | null;
  sender_account_id: string | null;
  sender_name: string | null;
  status: RecipientStatus;
  skip_reason: string | null;
  failure_reason: string | null;
  reason_details: string | null;
  needs_review: boolean;
  queued_at: string | null;
  sent_at: string | null;
  failed_at: string | null;
  replied_at: string | null;
  rendered_message: string | null;
}
export type OutreachEventType =
  | 'campaign:created'
  | 'campaign:started'
  | 'campaign:paused'
  | 'campaign:resumed'
  | 'campaign:cancelled'
  | 'campaign:completed'
  | 'recipient:queued'
  | 'recipient:sending'
  | 'recipient:sent'
  | 'recipient:skipped'
  | 'recipient:failed'
  | 'recipient:replied'
  | 'sender:unavailable';
export interface OutreachEvent {
  id: number;
  campaign_id: number | null;
  type: OutreachEventType;
  payload: Record<string, unknown>;
  created_at: string;
}
export interface LeadOutreach {
  do_not_contact: boolean;
  contacted: boolean;
  last_contacted_at: string | null;
  sender_name: string | null;
  conversation_status: 'waiting_reply' | 'replied' | 'stopped' | null;
  campaign: {
    id: number;
    recipient_id: number;
    name: string;
    status: RecipientStatus;
    reason: string | null;
    needs_review: boolean;
  } | null;
  pending_followups: number;
  messages: { direction: 'outbound' | 'inbound'; type: string; body: string; sent_at: string }[];
}
export type WorkspaceStatus = 'new' | 'contacted' | 'review' | RecipientStatus;
export interface WorkspaceUsername {
  username: string;
  status: WorkspaceStatus;
  reason: string | null;
  details: string | null;
}
export interface OutreachWorkspace {
  usernames: WorkspaceUsername[];
  messages: string[];
  sender_ids: string[];
  campaign: OutreachCampaign | null;
  running: boolean;
}

// ---------- iMessage through the iPhone ----------
export type IMessageJobStatus = 'pending' | 'issued' | 'execution_acknowledged' | 'uncertain' | 'failed';
export type IMessageProtocol = 'legacy' | 'v2';
export interface IMessageRecipient {
  phone: string;
  message: string;
  /** Latest status of the number in a real (non-test) campaign. */
  status: IMessageJobStatus | null;
}
export interface IMessageAttachment {
  id: string;
  filename: string;
  mime: string;
  size: number;
}
/** One message of a template or chain: text, files or both. */
export interface IMessagePart {
  text: string;
  attachments: IMessageAttachment[];
}
/** How the signed Shortcut sends a chain; `error` when it cannot. */
export interface IMessagePlan {
  messages: number;
  launches: number;
  error: string | null;
}
export interface IMessageTemplate {
  id: number;
  name: string;
  folder: string;
  parts: IMessagePart[];
  plan: IMessagePlan;
  updated_at: string;
}
export interface IMessageCampaign {
  id: number;
  protocol: IMessageProtocol;
  status: 'running' | 'paused' | 'stopped' | 'finished';
  is_test: boolean;
  delay_seconds: number;
  /** Jobs: one per recipient, or one per message of a chain. */
  total: number;
  /** Messages per recipient, launches of the Shortcut, and the launch the phone gets next. */
  messages: number;
  runs: number;
  next_run: number | null;
  created_at: string;
  finished_at: string | null;
  counts: Record<IMessageJobStatus, number>;
}
export interface IMessageJob {
  id: number;
  key: string;
  position: number;
  /** Index of the message in the chain. */
  step: number;
  phone: string;
  message: string;
  status: IMessageJobStatus;
  attempts: number;
  issued_at: string | null;
  text_acked_at: string | null;
  acked_at: string | null;
  ack_scope: 'text' | 'complete' | 'manual' | null;
  resolution: string | null;
  note: string | null;
}
export interface IMessageBridge {
  running: boolean;
  ip: string | null;
  port: number;
  addresses: string[];
  token_valid: boolean;
  token_expires_at: string | null;
  last_seen: { ip: string; at: string; user_agent: string } | null;
  connect_url: string | null;
  urls: Record<IMessageProtocol, string> | null;
  deep_links: Record<IMessageProtocol, string> | null;
}
export interface IMessageState {
  bridge: IMessageBridge;
  workspace: {
    recipients: IMessageRecipient[];
    /** Variants given to recipients in turn; {Phone} becomes the recipient. */
    messages: string[];
    attachments: IMessageAttachment[];
    /** A chain every recipient gets in order; empty when the variants are used. */
    sequence: IMessagePart[];
    plan: IMessagePlan | null;
    protocol: IMessageProtocol;
    shortcut_name: string;
    legacy_shortcut_name: string;
    delay_seconds: number;
  };
  campaign: IMessageCampaign | null;
  active: boolean;
  jobs: IMessageJob[];
  skipped?: number;
  added?: number;
}
export interface IMessageEvent {
  id: number;
  campaign_id: number | null;
  job_id: number | null;
  type: string;
  detail: string;
  created_at: string;
}
export interface IMessagePreview {
  protocol: IMessageProtocol;
  items: {
    phone: string;
    text: string;
    individual: boolean;
    messages?: { text: string; files: number; launch: number }[];
  }[];
  payload: Record<string, unknown>;
}

export type CrmId = 'instagram' | 'imessage';
export type CrmChannelKind = 'instagram' | 'email' | 'phone';
export interface CrmChannel {
  kind: CrmChannelKind;
  value: string;
}
export type CrmColor = 'violet' | 'blue' | 'green' | 'amber' | 'red' | 'pink' | 'slate';
export interface CrmStatus {
  label: string;
  color: CrmColor;
  /** Shown before the label; empty or absent when there is none. */
  emoji?: string;
}
export interface CrmContact {
  id: number;
  /** Shared CRM: whose contact it is; `mine` is false only in an admin's view of others. */
  owner_id: string | null;
  owner_name: string;
  mine: boolean;
  name: string;
  statuses: string[];
  channels: CrmChannel[];
  notes: string;
  last_contact_at: string | null;
  next_action: string;
  next_action_at: string | null;
  earned: number;
  potential: number;
  lead_id: number | null;
  deleted_at: string | null;
  attention: boolean;
}
export type CrmTab = 'all' | 'attention' | 'trash';
export interface CrmFilters {
  statuses?: string[];
  channels?: CrmChannelKind[];
  last?: 'never' | 'week' | 'month' | 'older';
  next?: 'none' | 'due' | 'planned';
  notes?: 'with' | 'without';
  money?: 'earned' | 'potential' | 'none';
}
export interface CrmList {
  items: CrmContact[];
  total: number;
  counts: Record<CrmTab, number>;
  totals: { earned: number; potential: number };
  statuses: CrmStatus[];
  labels: string[];
  /** Owners in an admin's copy, for the owner filter; empty for a user. */
  owners: { id: string; name: string; count: number; mine: boolean }[];
}
export interface CrmSource {
  id: 'leads' | CrmId;
  count: number;
}
export interface CrmImportResult {
  added: number;
  merged: number;
  skipped: number;
}

/** The signed-in account (core `account.state`); `configured` is false without a server. */
export interface AccountState {
  configured: boolean;
  signed_in: boolean;
  licensed: boolean;
  online: boolean;
  ready: boolean;
  user: { id: string; email: string; display_name: string; role: Role } | null;
  role: Role | null;
  reason: string | null;
}

/** admin: everything; moderator: every CRM; user: own CRM. */
export type Role = 'user' | 'moderator' | 'admin';
export type KeyRole = 'user' | 'moderator';
export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  blocked: boolean;
  created_at: string;
}
export interface AdminKey {
  id: string;
  key_hint: string;
  user_id: string | null;
  bound: boolean;
  note: string;
  /** The role the key grants on activation. */
  role: KeyRole;
  created_at: string | null;
  activated_at: string | null;
  revoked_at: string | null;
}
export interface AdminOverview {
  me: string;
  users: AdminUser[];
  keys: AdminKey[];
  key?: string;
}
