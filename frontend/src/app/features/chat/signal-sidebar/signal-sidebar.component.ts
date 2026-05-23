import { Component, Input, Output, EventEmitter, OnChanges, OnInit, OnDestroy, SimpleChanges, NgZone, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Agent, CryptexRingStatus } from '../../../core/models/agent.model';
import { ApiService } from '../../../core/services/api.service';
import { HormonePanelComponent } from '../hormone-panel/hormone-panel.component';
import { InfoModalComponent, InfoModalConfig } from '../../../shared/info-modal/info-modal.component';
import { CryptexVizComponent } from '../cryptex-viz/cryptex-viz.component';
import {
  SignalTag,
  tagColor as _tagColor,
  humanType as _humanType,
  humanizeLabel,
  extractDomain,
} from '../../../shared/signal-utils';

export type ActivityKind =
  | 'dream'           // passive daydream (replay / exploration)
  | 'active_dream'    // active dream (browse / bash / practice)
  | 'drive'           // autonomous drive action (web_search, wikipedia, etc.)
  | 'reach_out'       // proactive social initiative
  | 'finding'         // dream finding worth reporting
  | 'channel'         // external channel event (Telegram, WhatsApp, Email)
  | 'sleep_start'     // agent started sleeping
  | 'sleep_complete'  // sleep cycle completed
  | 'intention'       // prospective memory intention triggered
  | 'episode'         // narrative episode start/end
  | 'network'         // network switch event
  | 'regulation'      // emotional regulation applied
  | 'todo'            // todo task picked up / progress / completed
  | 'agentic_task';   // background agentic task execution

export interface DaydreamEntry {
  id: number;
  text: string;
  tags: SignalTag[];
  signals: number;
  factsStored: number;
  timestamp: Date;
}

export interface ActivityEntry {
  id: number;
  kind: ActivityKind;
  text: string;
  tags: SignalTag[];
  signals: number;
  factsStored: number;
  timestamp: Date;
  detail?: string;
  sources?: string[];
  relevance?: number;
  drive?: {
    name: string;
    actionType: string;
    domain: string;
    query: string;
    success: boolean;
    resultPreview: string;
  };
  metadata?: Record<string, any>;
}

interface AnimatedActivity extends ActivityEntry {
  state: 'entering' | 'visible' | 'exiting';
}

export interface ContextItem {
  index: number;
  signal_type: string;
  domain: string;
  content: string;
  source: string;
  timestamp: string;
  state: 'entering' | 'visible';
  _salience?: number;
}

const MAX_VISIBLE_ACTIVITIES = 10;

@Component({
  selector: 'app-signal-sidebar',
  standalone: true,
  imports: [CommonModule, FormsModule, HormonePanelComponent, InfoModalComponent, CryptexVizComponent],
  templateUrl: './signal-sidebar.component.html',
  styleUrl: './signal-sidebar.component.scss',
})
export class SignalSidebarComponent implements OnChanges, OnInit, OnDestroy {
  @Input() metadata: any = null;
  @Input() agent: Agent | null = null;
  @Input() daydreams: DaydreamEntry[] = [];
  @Input() activities: ActivityEntry[] = [];
  @Input() connectedChannels: string[] = [];

  @Output() channelThreadSelect = new EventEmitter<string>();

  @ViewChild('infoNeural') infoNeural?: InfoModalComponent;
  @ViewChild('infoMemory') infoMemory?: InfoModalComponent;
  @ViewChild('infoHormones') infoHormones?: InfoModalComponent;
  @ViewChild('infoActivity') infoActivity?: InfoModalComponent;
  @ViewChild('infoWm') infoWm?: InfoModalComponent;
  @ViewChild('infoNetwork') infoNetwork?: InfoModalComponent;

  neuralStateInfo: InfoModalConfig = {
    titleKey: 'info.neural_state.title',
    paragraphKeys: ['info.neural_state.p1', 'info.neural_state.p2', 'info.neural_state.p3'],
    icon: '💓',
  };
  memoryInfo: InfoModalConfig = {
    titleKey: 'info.memory.title',
    paragraphKeys: ['info.memory.p1', 'info.memory.p2', 'info.memory.p3'],
    icon: '🧠',
  };
  hormonesInfo: InfoModalConfig = {
    titleKey: 'info.hormones.title',
    paragraphKeys: ['info.hormones.p1'],
    icon: '🧪',
    legend: [
      { color: '#34d399', labelKey: 'info.hormones.legend.dopamine', descKey: 'info.hormones.legend.dopamine_desc' },
      { color: '#38bdf8', labelKey: 'info.hormones.legend.serotonin', descKey: 'info.hormones.legend.serotonin_desc' },
      { color: '#fbbf24', labelKey: 'info.hormones.legend.norepinephrine', descKey: 'info.hormones.legend.norepinephrine_desc' },
      { color: '#f87171', labelKey: 'info.hormones.legend.cortisol', descKey: 'info.hormones.legend.cortisol_desc' },
      { color: '#a78bfa', labelKey: 'info.hormones.legend.oxytocin', descKey: 'info.hormones.legend.oxytocin_desc' },
    ],
  };
  activityInfo: InfoModalConfig = {
    titleKey: 'info.activity.title',
    paragraphKeys: ['info.activity.p1'],
    icon: '⚡',
    legend: [
      { color: '#c084fc', labelKey: 'info.activity.legend.dream', descKey: 'info.activity.legend.dream_desc' },
      { color: '#c084fc', labelKey: 'info.activity.legend.active_dream', descKey: 'info.activity.legend.active_dream_desc' },
      { color: '#fbbf24', labelKey: 'info.activity.legend.drive', descKey: 'info.activity.legend.drive_desc' },
      { color: '#34d399', labelKey: 'info.activity.legend.reach_out', descKey: 'info.activity.legend.reach_out_desc' },
      { color: '#38bdf8', labelKey: 'info.activity.legend.finding', descKey: 'info.activity.legend.finding_desc' },
    ],
  };
  workingMemoryInfo: InfoModalConfig = {
    titleKey: 'info.working_memory.title',
    paragraphKeys: ['info.working_memory.p1'],
    icon: '🔮',
    legend: [
      { color: '#34d399', labelKey: 'info.working_memory.legend.learn', descKey: 'info.working_memory.legend.learn_desc' },
      { color: '#38bdf8', labelKey: 'info.working_memory.legend.bond', descKey: 'info.working_memory.legend.bond_desc' },
      { color: '#fbbf24', labelKey: 'info.working_memory.legend.evaluate', descKey: 'info.working_memory.legend.evaluate_desc' },
    ],
  };
  networkInfo: InfoModalConfig = {
    titleKey: 'info.network.title',
    paragraphKeys: ['info.network.p1', 'info.network.p2'],
    icon: '🌐',
    legend: [
      { color: '#38bdf8', labelKey: 'info.network.legend.ecn', descKey: 'info.network.legend.ecn_desc' },
      { color: '#fbbf24', labelKey: 'info.network.legend.sn', descKey: 'info.network.legend.sn_desc' },
      { color: '#a78bfa', labelKey: 'info.network.legend.dmn', descKey: 'info.network.legend.dmn_desc' },
    ],
  };

  activityList: AnimatedActivity[] = [];
  expandedActivityId: number | null = null;
  private knownActivityIds = new Set<number>();

  // Cryptex state
  contextLoading = false;
  editingIndex: number | null = null;
  editContent = '';
  private _lastAgentId: string | null = null;
  private _contextRefreshTimer: ReturnType<typeof setTimeout> | null = null;

  // Cryptex ring selection (driven by SVG viz click)
  selectedRingId: string | null = null;
  selectedRingPosition: string | null = null;

  constructor(
    private zone: NgZone,
    private api: ApiService,
  ) {}

  ngOnInit(): void {
    if (this.agent?.id) {
      this._lastAgentId = this.agent.id;
      this.loadContext();
    }
  }

  ngOnDestroy(): void {
    this.editingIndex = null;
    if (this._contextRefreshTimer) {
      clearTimeout(this._contextRefreshTimer);
      this._contextRefreshTimer = null;
    }
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['activities'] && this.activities) {
      this.syncActivities();
    }
    if (changes['daydreams'] && this.daydreams) {
      this.syncLegacyDaydreams();
    }
    if (changes['agent'] && this.agent?.id && this.agent.id !== this._lastAgentId) {
      this._lastAgentId = this.agent.id;
      this.loadContext();
    }
  }

  /** Migrate legacy daydream entries into the activity list. */
  private syncLegacyDaydreams(): void {
    for (const d of this.daydreams) {
      if (!this.knownActivityIds.has(d.id)) {
        const entry: ActivityEntry = { ...d, kind: 'dream' };
        this.addActivity(entry);
      }
    }
    this.trimOverflow();
  }

  private syncActivities(): void {
    let newItems = false;
    for (const a of this.activities) {
      if (!this.knownActivityIds.has(a.id)) {
        this.addActivity(a);
        newItems = true;
      } else {
        const existing = this.activityList.find(e => e.id === a.id);
        if (existing && (existing.detail !== a.detail || existing.text !== a.text)) {
          existing.detail = a.detail;
          existing.text = a.text;
          if (a.metadata) existing.metadata = a.metadata;
        }
      }
    }
    this.trimOverflow();

    if (newItems) {
      this._scheduleContextRefresh();
    }
  }

  private _scheduleContextRefresh(): void {
    if (this._contextRefreshTimer) clearTimeout(this._contextRefreshTimer);
    this._contextRefreshTimer = setTimeout(() => {
      this._contextRefreshTimer = null;
      this.loadContext();
    }, 3000);
  }

  private addActivity(entry: ActivityEntry): void {
    this.knownActivityIds.add(entry.id);
    this.activityList.unshift({ ...entry, state: 'entering' });

    // Sort by timestamp descending so newest is always on top,
    // regardless of WebSocket event arrival order.
    this.activityList.sort((a, b) => {
      const ta = a.timestamp instanceof Date ? a.timestamp.getTime() : 0;
      const tb = b.timestamp instanceof Date ? b.timestamp.getTime() : 0;
      return tb - ta;
    });

    setTimeout(() => {
      this.zone.run(() => {
        const item = this.activityList.find(e => e.id === entry.id);
        if (item) item.state = 'visible';
      });
    }, 400);
  }

  private trimOverflow(): void {
    while (this.activityList.filter(a => a.state !== 'exiting').length > MAX_VISIBLE_ACTIVITIES) {
      for (let i = this.activityList.length - 1; i >= 0; i--) {
        if (this.activityList[i].state !== 'exiting') {
          this.activityList[i].state = 'exiting';
          const exitId = this.activityList[i].id;
          setTimeout(() => {
            this.zone.run(() => {
              this.activityList = this.activityList.filter(a => a.id !== exitId);
              this.knownActivityIds.delete(exitId);
              if (this.expandedActivityId === exitId) {
                this.expandedActivityId = null;
              }
            });
          }, 450);
          break;
        }
      }
    }
  }

  toggleExpand(id: number): void {
    this.expandedActivityId = this.expandedActivityId === id ? null : id;
  }

  onActivityClick(item: AnimatedActivity, event: Event): void {
    if (item.kind === 'channel' && item.metadata?.['sessionKey']) {
      event.stopPropagation();
      this.channelThreadSelect.emit(item.metadata['sessionKey']);
    } else {
      this.toggleExpand(item.id);
    }
  }

  activityIcon(kind: ActivityKind): string {
    switch (kind) {
      case 'dream':          return '\u2726';
      case 'active_dream':   return '\uD83D\uDD2D';
      case 'drive':          return '\u26A1';
      case 'reach_out':      return '\uD83D\uDCAC';
      case 'finding':        return '\uD83D\uDCCB';
      case 'channel':        return '\uD83D\uDCE1';
      case 'sleep_start':    return '\uD83C\uDF19';
      case 'sleep_complete': return '\u2600\uFE0F';
      case 'intention':      return '\u23F0';
      case 'episode':        return '\uD83C\uDFAC';
      case 'network':        return '\uD83E\uDDE0';
      case 'regulation':     return '\uD83D\uDEE1\uFE0F';
      case 'todo':           return '\u2705';
      case 'agentic_task':   return '\u2699\uFE0F';
      default:               return '\u2022';
    }
  }

  activityLabel(kind: ActivityKind): string {
    switch (kind) {
      case 'dream':          return 'Daydream';
      case 'active_dream':   return 'Active Dream';
      case 'drive':          return 'Autonomous';
      case 'reach_out':      return 'Reach Out';
      case 'finding':        return 'Finding';
      case 'channel':        return 'Channel';
      case 'sleep_start':    return 'Sleep';
      case 'sleep_complete': return 'Woke Up';
      case 'intention':      return 'Intention';
      case 'episode':        return 'Episode';
      case 'network':        return 'Network';
      case 'regulation':     return 'Regulation';
      case 'todo':           return 'Task';
      case 'agentic_task':   return 'Background Task';
      default:               return 'Activity';
    }
  }

  /** Format a timestamp as HH:mm. */
  formatTime(date: Date): string {
    const d = date instanceof Date ? date : new Date(date);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // ── Heartbeat ──────────────────────────────────────────────

  get heartbeat(): any {
    return this.metadata?.heartbeat || {};
  }

  get bpm(): number {
    return this.heartbeat.bpm || 0;
  }

  /**
   * Show neural / temporal-self UI when we have a pulse or any digest fields.
   * Previously gated on bpm > 0 only, which hid Valence–Cohere after navigation
   * when metadata had digests but BPM was missing or zero.
   */
  get hasHeartbeatSection(): boolean {
    const hb = this.metadata?.heartbeat;
    if (!hb || typeof hb !== 'object') return false;
    if ((hb['bpm'] ?? 0) > 0) return true;
    const keys = [
      'valence', 'arousal', 'engagement', 'bonding', 'coherence', 'energy',
      'mood_label', 'momentum', 'felt_idle', 'beat_count',
    ] as const;
    for (const k of keys) {
      const v = hb[k];
      if (v !== undefined && v !== null && v !== '') return true;
    }
    if (hb['flow'] === true) return true;
    return false;
  }

  get beatCount(): number {
    return this.heartbeat.beat_count || 0;
  }

  get isAlive(): boolean {
    return this.heartbeat.alive !== false;
  }

  get valence(): number {
    return this.heartbeat.valence || 0;
  }

  get arousal(): number {
    return this.heartbeat.arousal || 0;
  }

  get engagement(): number {
    return this.heartbeat.engagement || 0;
  }

  get bonding(): number {
    return this.heartbeat.bonding || 0;
  }

  get coherence(): number {
    return this.heartbeat.coherence || 0;
  }

  get inFlow(): boolean {
    return this.heartbeat.flow === true;
  }

  /** CSS animation duration derived from BPM -- faster heart = faster pulse. */
  get heartbeatPeriod(): string {
    if (this.bpm <= 0) return '5s';
    return `${(60 / this.bpm).toFixed(2)}s`;
  }

  /** Valence mapped to a color: red (negative) → neutral → green (positive). */
  get valenceColor(): string {
    const v = this.valence;
    if (v >= 0.3) return '#34d399';   // green -- positive
    if (v >= 0.05) return '#6ee7b7';  // light green
    if (v > -0.05) return '#9ca3af';  // neutral grey
    if (v > -0.3) return '#fca5a5';   // light red
    return '#f87171';                  // red -- negative
  }

  /** Human label for the current state. */
  get stateLabel(): string {
    if (!this.isAlive) return 'Offline';
    if (this.inFlow) return 'Flow';
    if (this.bpm >= 80) return 'Active';
    if (this.bpm >= 40) return 'Resting';
    if (this.bpm >= 12) return 'Drowsy';
    return 'Deep sleep';
  }

  // ── Temporal Self ─────────────────────────────────────────

  get energy(): number {
    return this.heartbeat.energy ?? 0;
  }

  get energyPercent(): number {
    return Math.round(this.energy * 100);
  }

  get energyColor(): string {
    const e = this.energy;
    if (e > 0.6) return '#34d399';
    if (e > 0.3) return '#fbbf24';
    return '#f87171';
  }

  get moodLabel(): string {
    return this.heartbeat.mood_label || '';
  }

  get momentum(): string {
    return this.heartbeat.momentum || '';
  }

  get momentumIcon(): string {
    switch (this.momentum) {
      case 'building': return '\u2191';
      case 'fading':   return '\u2193';
      case 'stable':   return '\u2013';
      default:         return '';
    }
  }

  get deltaValence(): number {
    return this.heartbeat.delta_valence ?? 0;
  }

  get deltaArousal(): number {
    return this.heartbeat.delta_arousal ?? 0;
  }

  get deltaCoherence(): number {
    return this.heartbeat.delta_coherence ?? 0;
  }

  get feltIdle(): string {
    const v = this.heartbeat.felt_idle;
    if (v === true) return 'idle';
    if (typeof v === 'string') return v;
    return '';
  }

  deltaArrow(delta: number): string {
    if (Math.abs(delta) < 0.02) return '';
    return delta > 0 ? '\u2191' : '\u2193';
  }

  deltaColor(delta: number): string {
    if (Math.abs(delta) < 0.02) return 'transparent';
    return delta > 0 ? '#34d399' : '#f87171';
  }

  // ── Narrative Self ─────────────────────────────────────────

  get narrativeCoherence(): number {
    return this.metadata?.narrative?.narrative_coherence ?? this.heartbeat.narrative_coherence ?? 0;
  }

  get narrativeCoherenceLabel(): string {
    return this.metadata?.narrative?.coherence_label ?? this.heartbeat.coherence_label ?? '';
  }

  get currentEpisode(): any {
    return this.metadata?.narrative?.current_episode ?? null;
  }

  get episodeArc(): string {
    return this.currentEpisode?.arc || this.currentEpisode?.arc_summary || this.heartbeat.episode_arc || '';
  }

  get episodeMoodColor(): string {
    const arc = this.episodeArc.toLowerCase();
    if (['warm', 'serene', 'aligned', 'curious'].some(m => arc.includes(m))) return '#34d399';
    if (['playful', 'engaged', 'focused'].some(m => arc.includes(m))) return '#6ee7b7';
    if (['tense', 'anxious', 'frustrated'].some(m => arc.includes(m))) return '#f87171';
    if (['conflicted'].some(m => arc.includes(m))) return '#fbbf24';
    return '#94a3b8';
  }

  // ── Theory of Mind ────────────────────────────────────────

  get userModel(): any {
    return this.metadata?.theory_of_mind?.user_model ?? null;
  }

  get convTemperature(): number {
    return this.metadata?.theory_of_mind?.temperature?.temperature ?? this.heartbeat.conv_temperature ?? 0;
  }

  get convTemperatureLabel(): string {
    return this.metadata?.theory_of_mind?.temperature?.label ?? this.heartbeat.conv_temperature_label ?? '';
  }

  // ── Network Dynamics ──────────────────────────────────────

  get networkDynamics(): any {
    return this.metadata?.network_dynamics ?? null;
  }

  get dominantNetwork(): string {
    return this.networkDynamics?.dominant_label ?? this.networkDynamics?.dominant ?? this.heartbeat.dominant_network ?? '';
  }

  get networkEcn(): number {
    return this.networkDynamics?.ecn ?? this.heartbeat.network_ecn ?? 0;
  }

  get networkSn(): number {
    return this.networkDynamics?.sn ?? this.heartbeat.network_sn ?? 0;
  }

  get networkDmn(): number {
    return this.networkDynamics?.dmn ?? this.heartbeat.network_dmn ?? 0;
  }

  // ── Predictive Processing ─────────────────────────────────

  get predictionError(): number {
    return this.metadata?.predictive_processing?.average_pe ?? this.heartbeat.prediction_error ?? 0;
  }

  // ── Working Memory (front-brain slots) ────────────────────

  get wmStatus(): any {
    return this.metadata?.working_memory ?? null;
  }

  get wmSlotCount(): number {
    return this.wmStatus?.slot_count ?? 0;
  }

  get wmMaxSlots(): number {
    return this.wmStatus?.max_slots ?? 0;
  }

  get hasWmSlots(): boolean {
    return this.wmStatus != null && (this.wmStatus.slots?.length > 0 || this.wmStatus.goals?.length > 0 || this.wmStatus.instructions?.length > 0);
  }

  get wmActiveWorkspace(): string {
    return this.wmStatus?.active_workspace || '';
  }

  get wmConsolidation(): string {
    return this.wmStatus?.consolidation_context || '';
  }

  get wmOrchTeams(): any[] {
    return this.wmStatus?.orch_teams ?? [];
  }

  get wmOrchEscalations(): any[] {
    return this.wmStatus?.orch_escalations ?? [];
  }

  get wmOrchDecisionCount(): number {
    return this.wmStatus?.orch_decision_count ?? 0;
  }

  get hasOrchState(): boolean {
    return this.wmOrchTeams.length > 0 || this.wmOrchEscalations.length > 0 || this.wmOrchDecisionCount > 0;
  }

  get cryptexActiveProject(): string {
    return this.wmStatus?.active_project || '';
  }

  get cryptexActiveDomain(): string {
    return this.wmStatus?.active_domain || '';
  }

  get cryptexProjects(): string[] {
    return this.wmStatus?.projects ?? [];
  }

  get cryptexRingsRaw(): CryptexRingStatus[] {
    const rings = this.wmStatus?.rings;
    if (!rings) return [];
    return Object.values(rings) as CryptexRingStatus[];
  }

  get cryptexRings(): any[] {
    const rings = this.wmStatus?.rings;
    if (!rings) return [];
    return Object.values(rings).map((r: any) => ({
      ...r,
      totalSlots: Object.values(r.positions || {}).reduce((a: number, b: any) => a + (b as number), 0),
      positionList: Object.entries(r.positions || {}).map(([id, count]) => ({
        id,
        count: count as number,
        isActive: id === r.active_position,
        slots: r.slot_details?.[id] ?? [],
      })),
    }));
  }

  get selectedRingData(): any | null {
    if (!this.selectedRingId) return null;
    return this.cryptexRings.find((r: any) => r.ring_id === this.selectedRingId) ?? null;
  }

  onRingSelect(ringId: string): void {
    if (this.selectedRingId === ringId) {
      this.selectedRingId = null;
      this.selectedRingPosition = null;
    } else {
      this.selectedRingId = ringId;
      const ring = this.cryptexRings.find((r: any) => r.ring_id === ringId);
      this.selectedRingPosition = ring?.active_position || ring?.positionList?.[0]?.id || null;
    }
  }

  selectRingPosition(posId: string): void {
    this.selectedRingPosition = posId;
  }

  formatSlotAge(ageSeconds: number): string {
    if (ageSeconds < 60) return `${ageSeconds}s`;
    if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m`;
    if (ageSeconds < 86400) return `${Math.floor(ageSeconds / 3600)}h`;
    return `${Math.floor(ageSeconds / 86400)}d`;
  }

  get hasCryptexState(): boolean {
    return this.cryptexProjects.length > 0 || this.cryptexRingsRaw.length > 0;
  }

  // ── Token budget ────────────────────────────────────────────

  get tokenBudgetPercent(): number {
    const tb = this.wmStatus?.token_budget;
    if (!tb || !tb.limit) return 0;
    return Math.min(100, Math.round((tb.estimated_used / tb.limit) * 100));
  }

  get tokenBudgetColor(): string {
    const pct = this.tokenBudgetPercent;
    if (pct >= 80) return '#f87171';
    if (pct >= 50) return '#fbbf24';
    return '#34d399';
  }

  get tokenBudgetLabel(): string {
    const tb = this.wmStatus?.token_budget;
    if (!tb) return '';
    const usedK = (tb.estimated_used / 1000).toFixed(1);
    const limitK = (tb.limit / 1000).toFixed(0);
    return `${usedK}k / ${limitK}k tokens`;
  }

  // ── Access tier helpers ─────────────────────────────────────

  accessIcon(tier: string): string {
    switch (tier) {
      case 'genesis':  return '\uD83D\uDD12';
      case 'system':   return '\u2699\uFE0F';
      case 'malleable': return '\u270F\uFE0F';
      case 'session':  return '\u23F1\uFE0F';
      default:         return '\u25CB';
    }
  }

  accessLabel(tier: string): string {
    switch (tier) {
      case 'genesis':  return 'Genesis (locked)';
      case 'system':   return 'System';
      case 'malleable': return 'Malleable';
      case 'session':  return 'Session';
      default:         return tier;
    }
  }

  // ── Hormones ─────────────────────────────────────────────

  get hormones(): Record<string, number> {
    return this.metadata?.hormones || this.agent?.runtime?.hormones || {};
  }

  get signals(): any[] {
    return this.metadata?.signals || [];
  }

  get factsCount(): number {
    return this.metadata?.facts_in_memory || this.agent?.runtime?.facts_in_memory || 0;
  }

  get metaWeight(): number {
    return this.metadata?.meta_weight || 0;
  }

  get sleepCount(): number {
    return this.metadata?.sleep_count || 0;
  }

  get ansState(): string {
    const state = this.metadata?.ans?.state || 'unknown';
    return state.charAt(0).toUpperCase() + state.slice(1);
  }

  get ansSignals(): number {
    return this.metadata?.ans?.total_signals || 0;
  }

  /** Get the dot color for a signal type. */
  signalColor(type: string): string {
    return _tagColor(type);
  }

  /** Human-readable signal type label. */
  humanType(type: string): string {
    return _humanType(type);
  }

  /** Human-readable domain label (handles compound signal types). */
  humanDomain(sig: any): string {
    const domain = extractDomain(sig);
    return domain ? humanizeLabel(domain) : '';
  }

  // ── Cryptex Data ────────────────────────────────────────────

  loadContext(): void {
    if (!this.agent?.id) return;
    this.contextLoading = true;
    this.api.getWorkingMemory(this.agent.id).subscribe({
      next: (res) => {
        this.zone.run(() => {
          if (this.metadata) {
            this.metadata.working_memory = res;
          }
          this.contextLoading = false;
        });
      },
      error: () => {
        this.contextLoading = false;
      },
    });
  }

  onNewLearning(): void {
    this.loadContext();
  }

  deleteInstruction(instrIdx: number): void {
    if (!this.agent?.id) return;
    this.api.deleteWmInstruction(this.agent.id, instrIdx).subscribe({
      next: () => {
        this.zone.run(() => { this.loadContext(); });
      },
    });
  }

  cancelEdit(): void {
    this.editingIndex = null;
    this.editContent = '';
  }

  startSlotEdit(index: number, content: string): void {
    this.editingIndex = index;
    this.editContent = content;
  }

  saveInstructionEdit(instrIdx: number): void {
    if (!this.agent?.id || !this.editContent.trim()) {
      this.cancelEdit();
      return;
    }
    const newContent = this.editContent.trim();
    this.api.updateWmInstruction(this.agent.id, instrIdx, newContent).subscribe({
      next: () => {
        this.zone.run(() => {
          this.editingIndex = null;
          this.editContent = '';
          this.loadContext();
        });
      },
      error: () => { this.cancelEdit(); },
    });
  }

  onEditKeydown(event: KeyboardEvent, instrIdx: number): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.saveInstructionEdit(instrIdx);
    } else if (event.key === 'Escape') {
      this.cancelEdit();
    }
  }

  slotSalienceOpacity(salience: number): number {
    return Math.max(0.4, Math.min(1.0, salience));
  }
}
