// ─── Core Agent ──────────────────────────────────────────────────
export interface Agent {
  id: string;
  /** Nest DB id after desktop sync (for API keys / cloud features). */
  cloudId?: string;
  userId: string;
  runtimeAgentId: string;
  name: string | null;
  genesisVersion: string;
  status: string;
  createdAt: string;
  runtime?: AgentRuntimeStatus;
  userPaused?: boolean;
}

export interface CreateAgentRequest {
  genesisVersion?: string;
  name?: string;
  sovereignty?: string;
  soulWish?: string;
}

// ─── Genesis Templates ──────────────────────────────────────────
export interface GenesisTemplate {
  version: string;
  base_model: string;
  description: string;
  minted_at: string | null;
  profile: string;
  educated: boolean;
  education: GenesisEducation | null;
  has_epochs: boolean;
  error?: string;
}

export interface GenesisEducation {
  school: string;
  graduated: boolean;
  graduated_at: string;
  total_facts: number;
  total_sleeps: number;
  source_agent: string;
}

// ─── Python runtime live status ───────────────────────────────────
export interface AgentRuntimeStatus {
  agent_id?: string;
  status?: string;
  initialized?: boolean;
  turn_count?: number;
  sleep_count?: number;
  facts_in_memory?: number;
  hormones?: Record<string, number>;
  ans?: { state: string; total_signals: number; learnable_signals: number };
  thalamus?: { calibrated: boolean; n_bands: number };
  in_vram?: boolean;
  name?: string;
  genesis_version?: string;
  heartbeat?: HeartbeatStatus;
  working_memory?: WorkingMemoryStatus;
  narrative?: NarrativeStatus;
  theory_of_mind?: TheoryOfMindStatus;
  predictive_processing?: PredictiveStatus;
  network_dynamics?: NetworkDynamicsStatus;
  [key: string]: any;
}

// ─── Heartbeat (Temporal Self) ──────────────────────────────────
export interface HeartbeatStatus {
  bpm?: number;
  beat_count?: number;
  alive?: boolean;
  valence?: number;
  arousal?: number;
  engagement?: number;
  bonding?: number;
  coherence?: number;
  flow?: string;
  delta_valence?: number;
  delta_arousal?: number;
  delta_coherence?: number;
  mood_label?: string;
  energy?: number;
  momentum?: string;
  felt_idle?: string | boolean;
  narrative_coherence?: number;
  coherence_label?: string;
  regulation?: string | null;
  episode_arc?: string | null;
  conv_temperature?: number;
  conv_temperature_label?: string;
  prediction_error?: number;
  uncertainty?: number;
  pe_surprise?: string | null;
  network_ecn?: number;
  network_sn?: number;
  network_dmn?: number;
  dominant_network?: string | null;
}

// ─── Working Memory ─────────────────────────────────────────────
export interface WMSlot {
  type: string;
  content: string;
  salience: number;
  domain: string;
}

export interface WMGoal {
  level: string;
  content: string;
}

export interface WMIntention {
  trigger: string;
  content: string;
}

export interface WMInstruction {
  content: string;
  source: string;
  salience: number;
}

export interface WMOrchTeam {
  team_id: string;
  plan_id: string;
  status: string;
  member_count: number;
  done_count: number;
  failed_count: number;
}

export interface WMOrchEscalation {
  team_id: string;
  member_idx: number;
  context: string;
  age_seconds: number;
}

export interface CryptexSlotDetail {
  domain: string;
  slot_type: string;
  salience: number;
  content: string;
  age_s: number;
  access: 'genesis' | 'system' | 'malleable' | 'session';
  source: string;
}

export interface CryptexRingStatus {
  ring_id: string;
  category: string;
  display_name: string;
  active_position: string;
  positions: Record<string, number>;
  total_writes: number;
  max_slots: number;
  slot_details?: Record<string, CryptexSlotDetail[]>;
}

export interface TokenBudget {
  limit: number;
  estimated_used: number;
}

export interface CryptexState {
  active_project: string;
  active_domain: string;
  projects: string[];
  domains: string[];
  rings: Record<string, CryptexRingStatus>;
}

export interface WorkingMemoryStatus {
  slot_count: number;
  max_slots: number;
  goal_count: number;
  intention_count: number;
  instruction_count?: number;
  slots: WMSlot[];
  goals: WMGoal[];
  intentions: WMIntention[];
  instructions?: WMInstruction[];
  plan_position?: string | null;
  active_workspace?: string;
  common_slot_count?: number;
  personal_slots?: WMSlot[];
  personal_slot_count?: number;
  personal_goals?: WMGoal[];
  professional_slots?: WMSlot[];
  professional_slot_count?: number;
  professional_goals?: WMGoal[];
  consolidation_context?: string;
  orch_teams?: WMOrchTeam[];
  orch_escalations?: WMOrchEscalation[];
  orch_decision_count?: number;
  active_project?: string;
  active_domain?: string;
  projects?: string[];
  domains?: string[];
  rings?: Record<string, CryptexRingStatus>;
  token_budget?: TokenBudget;
}

// ─── Narrative Self ─────────────────────────────────────────────
export interface ArcSnapshot {
  turn: number;
  v: number;
  a: number;
  mood: string;
  t: number;
}

export interface NarrativeEpisode {
  index: number;
  title: string;
  is_active?: boolean;
  turns: number;
  arc_summary: string;
  arc_snapshots?: ArcSnapshot[];
  duration_min?: number;
  start_time?: number;
  end_time?: number;
  opening_mood?: string;
  closing_mood?: string;
  dominant_emotion?: string;
  peak_resonance: number;
  peak_cortisol?: number;
  peak_engagement?: number;
  coherence_contribution?: number;
  domains?: string[];
  topics?: string[];
  summary?: string;
}

export interface NarrativeBlock {
  timestamp: number;
  block_type: string;
  content: string;
  source_episode?: string;
  domains?: string[];
  coherence_delta?: number;
}

export interface NarrativeStatus {
  narrative_coherence: number;
  coherence_label: string;
  active_strategy: string | null;
  regulation_count: number;
  episode_count: number;
  current_episode: NarrativeEpisode | null;
  episodes: NarrativeEpisode[];
  values?: string[];
  soul_wish?: string;
  narrative_blocks?: NarrativeBlock[];
  /** @deprecated Use episodes instead */
  recent_episodes?: NarrativeEpisode[];
}

// ─── Theory of Mind ─────────────────────────────────────────────
export interface UserModel {
  user_id: string;
  turn_count: number;
  style: string;
  patience: number;
  top_interests: string[] | [string, number][];
  expertise: Record<string, number>;
  channel_styles?: Record<string, Record<string, number>>;
}

export interface TheoryOfMindStatus {
  active_user: string | null;
  user_count: number;
  temperature: { temperature: number; label: string; reading_count: number };
  user_model: UserModel;
  users?: UserModel[];
}

// ─── Predictive Processing ──────────────────────────────────────
export interface PredictionDetail {
  turn: number;
  pe: number;
  confidence: number;
  expected_domain: string;
  actual_domain: string;
  pe_components?: Record<string, number>;
}

export interface UncertainDomain {
  domain: string;
  uncertainty: number;
}

export interface PredictiveStatus {
  prediction_count: number;
  average_pe: number;
  surprise_count: number;
  last_prediction: PredictionDetail | null;
  high_uncertainty_domains: UncertainDomain[];
}

// ─── Network Dynamics ───────────────────────────────────────────
export interface NetworkTransition {
  timestamp?: number;
  from_network: string;
  to_network: string;
  trigger: string;
  sn_level?: number;
  ecn_level?: number;
  dmn_level?: number;
}

export interface NetworkDynamicsStatus {
  ecn: number;
  sn: number;
  dmn: number;
  dominant: string;
  dominant_label: string;
  transition_count: number;
  recent_transitions: NetworkTransition[];
}

// ─── Memory Chain ────────────────────────────────────────────────
export interface ChainState {
  agent_id: string;
  base_model: string;
  base_model_label?: string;
  block_count?: number;
  sovereignty_mode: string;
  current_height: number;
  genesis_hash: string;
  soul_hash: string;
  active_epoch: Block | null;
  active_deltas: Block[];
  frozen_epochs: Block[];
  consolidated: Block[];
  flip_threshold: number;
  flip_window_days: number;
}

export interface Block {
  height: number;
  block_hash: string;
  parent_hash: string;
  block_type: 'delta' | 'epoch' | 'genesis';
  delta_path: string;
  timestamp: string;
  aku_count: number;
  metadata?: Record<string, any>;
}

// ─── Facts ───────────────────────────────────────────────────────
export interface Fact {
  id: number;
  domain_path: string;
  current_value: string;
  canonical_question: string | null;
  block_height: number;
  flip_count: number;
  is_fluid: boolean;
  meta_layer: string | null;
  hormonal_fingerprint: string | null;
  last_modified: string;
  created_at: string;
}

export interface FactsResponse {
  facts: Fact[];
  total: number;
  page: number;
  limit: number;
}

// ─── Events ──────────────────────────────────────────────────────
export interface EventLog {
  ts: string;
  event: string;
  data: Record<string, any>;
}

export interface EventsResponse {
  events: EventLog[];
  count: number;
}

// ─── Conversation ────────────────────────────────────────────────
export interface ConversationMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

// ─── Hormones & Signals ─────────────────────────────────────────
export interface HormoneHistory {
  hormones: Record<string, { ts: string; turn: number; value: number }[]>;
  data_points: number;
}

export interface NetworkHistory {
  network: Record<string, { ts: string; turn: number; value: number }[]>;
  data_points: number;
}

export interface SignalHistory {
  signals: { ts: string; signal_type: string; domain_path: string; content: string; turn: number; meta_layer: string }[];
  type_counts: Record<string, number>;
  total: number;
}

// ─── Memory Tiers ────────────────────────────────────────────────
export interface MemoryTiers {
  active_deltas: Block[];
  active_epoch: Block | null;
  frozen_epochs: Block[];
  consolidated: Block[];
  current_height: number;
  genesis_hash: string;
  soul_hash: string;
  adapter_directories: string[];
}

// ─── Soul Package ────────────────────────────────────────────────
export interface SoulImportResult {
  status: string;
  manifest: Record<string, any>;
}

export interface ForkResult {
  status: string;
  new_agent_id: string;
  fork_height: number;
  facts_copied: number;
  chain_height: number;
  [key: string]: any;
}
