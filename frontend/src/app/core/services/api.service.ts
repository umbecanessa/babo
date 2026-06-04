import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { Observable, map, of, catchError, switchMap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { isDesktopShell, nestjsRootFromApiBase, readBaboBoot } from '../desktop-boot';
import { PlatformService } from './platform.service';
import {
  Agent,
  CreateAgentRequest,
  AgentRuntimeStatus,
  ChainState,
  FactsResponse,
  EventsResponse,
  HormoneHistory,
  NetworkHistory,
  SignalHistory,
  ConversationMessage,
  WorkingMemoryStatus,
  TheoryOfMindStatus,
  NarrativeStatus,
  NetworkDynamicsStatus,
  GenesisTemplate,
  MemoryTiers,
  SoulImportResult,
  ForkResult,
} from '../models/agent.model';

export interface FileAttachment {
  name: string;
  path: string;
  size: number;
  mime_type: string;
}

/** Detached project dev server / background process from agent bash tool. */
export interface ProjectProcess {
  pid: number;
  kind: string;
  label: string;
  command: string;
  cwd: string;
  started_at: number;
}

/**
 * API Service with dual routing:
 *
 * - **Auth routes** (login, register, API keys, settings) -> NestJS backend
 * - **Agent routes** (agents, chat, tools, brain, memory) -> Local runtime
 *   (in Electron) or NestJS proxy (in browser)
 *
 * In Electron mode, `runtimeUrl` points to the local Python runtime
 * at http://127.0.0.1:9222 and can be dynamically updated via IPC.
 * In browser mode, both `API` and `RUNTIME` point to the NestJS proxy.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  /** NestJS backend (auth, admin, API keys) */
  private API = environment.apiUrl;

  /** Agent runtime — proxy in browser, direct Python in Electron */
  private RUNTIME = (environment as any).runtimeUrl || environment.apiUrl;

  /** Resolves after Electron loads nestjsUrl / runtimePort from nls-config.json */
  private readonly urlsReady: Promise<void>;

  /** Last successful agent list — survives dashboard destroy/recreate on nav back. */
  private agentsCache: Agent[] = [];

  private runtimeHealthy = false;
  private runtimeHealthCheckedAt = 0;
  private static readonly RUNTIME_HEALTH_TTL_MS = 30_000;

  /**
   * Base URL for agent CRUD.
   * Electron: local Python runtime (direct).
   * Browser: NestJS API (DB with ownership filtering).
   */
  private get AGENTS(): string {
    return this.platform.isElectron ? this.RUNTIME : this.API;
  }

  get runtimeBase(): string {
    return this.RUNTIME;
  }

  get apiBase(): string {
    return this.API;
  }

  constructor(
    private http: HttpClient,
    private platform: PlatformService,
  ) {
    this.applyBootConfig();
    this.urlsReady = isDesktopShell()
      ? this.initElectronUrls()
      : Promise.resolve();
    this.listenForConfigChanges();
  }

  /** Preload injects nls.boot before Angular bootstraps (no localhost race). */
  private applyBootConfig(): void {
    const boot = readBaboBoot();
    if (!boot) {
      return;
    }
    if (boot.apiUrl) {
      this.API = boot.apiUrl;
    }
    if (boot.runtimeUrl) {
      this.RUNTIME = boot.runtimeUrl;
    }
  }

  private listenForConfigChanges(): void {
    const nls = (window as unknown as { nls?: { on?: Function } }).nls;
    nls?.on?.('config:changed', () => {
      this.applyBootConfig();
      this.invalidateRuntimeHealth();
      void this.initElectronUrls();
    });
  }

  whenReady(): Promise<void> {
    return this.urlsReady;
  }

  /** Resolved NestJS API base (includes `/api` prefix). */
  getApiBaseUrl(): string {
    return this.API;
  }

  getCachedAgents(): Agent[] {
    return this.agentsCache;
  }

  /** Fast path when returning to dashboard while runtime is already up. */
  async isRuntimeReady(force = false): Promise<boolean> {
    const now = Date.now();
    if (
      !force
      && this.runtimeHealthy
      && now - this.runtimeHealthCheckedAt < ApiService.RUNTIME_HEALTH_TTL_MS
    ) {
      return true;
    }
    try {
      await firstValueFrom(this.getHealth());
      this.runtimeHealthy = true;
      this.runtimeHealthCheckedAt = now;
      return true;
    } catch {
      this.runtimeHealthy = false;
      this.runtimeHealthCheckedAt = now;
      return false;
    }
  }

  markRuntimeReady(): void {
    this.runtimeHealthy = true;
    this.runtimeHealthCheckedAt = Date.now();
  }

  invalidateRuntimeHealth(): void {
    this.runtimeHealthy = false;
    this.runtimeHealthCheckedAt = 0;
  }

  /** NestJS global prefix is `/api` (see backend main.ts). */
  static nestjsApiBase(nestjsUrl: string): string {
    const base = nestjsUrl.trim().replace(/\/+$/, '');
    return base.endsWith('/api') ? base : `${base}/api`;
  }

  /**
   * In Electron mode, fetch NestJS + runtime URLs from the main process
   * (setup wizard writes nestjsUrl, e.g. https://api.babo.agency).
   */
  private async initElectronUrls(): Promise<void> {
    const nls = (window as unknown as {
      nls?: {
        config?: { get: () => Promise<{ nestjsUrl?: string; runtimePort?: number }> };
        getUrls?: () => Promise<{ apiUrl?: string; runtimeUrl?: string; nestjsUrl?: string }>;
      };
    }).nls;
    if (!nls) {
      return;
    }

    this.applyBootConfig();

    try {
      const cfg = await nls.config?.get?.();
      if (cfg?.nestjsUrl) {
        this.API = ApiService.nestjsApiBase(cfg.nestjsUrl);
      }
    } catch {
      /* keep boot / defaults */
    }

    try {
      const urls = await nls.getUrls?.();
      if (urls?.runtimeUrl) {
        this.RUNTIME = urls.runtimeUrl;
      }
      if (urls?.apiUrl) {
        this.API = urls.apiUrl;
      } else if (urls?.nestjsUrl) {
        this.API = ApiService.nestjsApiBase(urls.nestjsUrl);
      }
    } catch {
      /* keep config / boot */
    }
  }

  /** NestJS root URL (no /api) — for main-process ping. */
  nestjsRoot(): string {
    return nestjsRootFromApiBase(this.API);
  }

  // ─── Genesis Templates ────────────────────────────────────────
  getGenesisTemplates(): Observable<GenesisTemplate[]> {
    return this.http.get<any[]>(`${this.AGENTS}/agents/genesis`).pipe(
      map(list => list.map(t => this.normalizeTemplate(t))),
    );
  }

  private normalizeTemplate(raw: any): GenesisTemplate {
    const edu = raw.education || null;
    return {
      version: raw.version || '',
      base_model: raw.base_model || '',
      description: raw.description || '',
      minted_at: raw.minted_at || null,
      profile: raw.profile || '',
      educated: raw.educated ?? (edu?.graduated === true),
      education: edu,
      has_epochs: raw.has_epochs ?? (raw.has_adapters === true),
    };
  }

  // ─── Agents (CRUD) ────────────────────────────────────────────
  getAgents(): Observable<Agent[]> {
    return this.http.get<any[]>(`${this.AGENTS}/agents`).pipe(
      map(list => {
        const agents = list.map(a => this.normalizeAgent(a));
        this.agentsCache = agents;
        return agents;
      }),
    );
  }

  getAgent(id: string): Observable<Agent> {
    return this.http.get<any>(`${this.AGENTS}/agents/${id}`).pipe(
      map(a => this.normalizeAgent(a)),
    );
  }

  createAgent(data: CreateAgentRequest): Observable<Agent> {
    return this.http.post<any>(`${this.AGENTS}/agents`, data).pipe(
      map(a => this.normalizeAgent(a)),
      switchMap(agent => this.attachCloudAgentRecord(agent)),
    );
  }

  /**
   * After local runtime creation, register in Nest so cloud keys / relay use DB ids.
   */
  private attachCloudAgentRecord(agent: Agent): Observable<Agent> {
    if (!this.platform.isElectron || !agent.runtimeAgentId) {
      return of(agent);
    }
    return this.syncAgentToCloud(agent).pipe(
      map((row: any) => ({
        ...agent,
        cloudId: row?.id,
        userId: row?.userId || agent.userId,
      })),
      catchError(() => of(agent)),
    );
  }

  /** Register a locally-created agent in the NestJS DB for web/relay access. */
  private syncAgentToCloud(agent: Agent): Observable<any> {
    return this.http.post(`${this.API}/agents/sync`, {
      runtimeAgentId: agent.runtimeAgentId,
      name: agent.name,
      genesisVersion: agent.genesisVersion || 'default',
    });
  }

  deleteAgent(id: string): Observable<any> {
    return this.http.delete(`${this.AGENTS}/agents/${id}`);
  }

  pauseAgent(id: string): Observable<any> {
    return this.http.post(`${this.AGENTS}/agents/${id}/pause`, {});
  }

  unpauseAgent(id: string): Observable<any> {
    return this.http.post(`${this.AGENTS}/agents/${id}/unpause`, {});
  }

  // ─── Agent Detail ─────────────────────────────────────────────
  getAgentStatus(agentId: string): Observable<AgentRuntimeStatus> {
    return this.http.get<AgentRuntimeStatus>(`${this.RUNTIME}/agents/${agentId}`);
  }

  listProjectProcesses(agentId: string): Observable<{
    processes: ProjectProcess[];
    agentic_running: boolean;
  }> {
    return this.http
      .get<{ processes: ProjectProcess[]; agentic_running?: boolean }>(
        `${this.RUNTIME}/agents/${agentId}/processes`,
      )
      .pipe(map(res => ({
        processes: res.processes || [],
        agentic_running: res.agentic_running ?? false,
      })));
  }

  killProjectProcess(agentId: string, pid: number): Observable<ProjectProcess[]> {
    return this.http
      .delete<{ processes: ProjectProcess[] }>(`${this.RUNTIME}/agents/${agentId}/processes/${pid}`)
      .pipe(map(res => res.processes || []));
  }

  getAgentChain(agentId: string): Observable<ChainState> {
    return this.http.get<ChainState>(`${this.RUNTIME}/admin/agents/${agentId}/chain`);
  }

  getAgentFacts(agentId: string, opts?: { search?: string; limit?: number; page?: number }): Observable<FactsResponse> {
    let params = new HttpParams();
    if (opts?.search) params = params.set('search', opts.search);
    if (opts?.limit) params = params.set('limit', opts.limit.toString());
    if (opts?.page) params = params.set('page', opts.page.toString());
    return this.http.get<FactsResponse>(`${this.RUNTIME}/admin/agents/${agentId}/facts`, { params });
  }

  toggleFactFluid(agentId: string, factId: number, isFluid: boolean): Observable<any> {
    return this.http.patch(`${this.RUNTIME}/admin/agents/${agentId}/facts/${factId}/fluid`, { is_fluid: isFluid });
  }

  getAgentEvents(agentId: string, opts?: { event_type?: string; limit?: number }): Observable<EventsResponse> {
    let params = new HttpParams();
    if (opts?.event_type) params = params.set('event_type', opts.event_type);
    if (opts?.limit) params = params.set('limit', opts.limit.toString());
    return this.http.get<EventsResponse>(`${this.RUNTIME}/admin/agents/${agentId}/events`, { params });
  }

  getAgentConversation(agentId: string): Observable<{ messages: ConversationMessage[] }> {
    return this.http.get<{ messages: ConversationMessage[] }>(`${this.RUNTIME}/admin/agents/${agentId}/conversation`);
  }

  getAgentConfig(agentId: string): Observable<Record<string, any>> {
    return this.http.get<Record<string, any>>(`${this.RUNTIME}/admin/agents/${agentId}/config`);
  }

  getHormoneHistory(agentId: string): Observable<HormoneHistory> {
    return this.http.get<HormoneHistory>(`${this.RUNTIME}/admin/agents/${agentId}/hormones/history`);
  }

  getSignalHistory(agentId: string): Observable<SignalHistory> {
    return this.http.get<SignalHistory>(`${this.RUNTIME}/admin/agents/${agentId}/signals/history`);
  }

  getNetworkHistory(agentId: string): Observable<NetworkHistory> {
    return this.http.get<NetworkHistory>(`${this.RUNTIME}/admin/agents/${agentId}/network/history`);
  }

  updateCircadianConfig(agentId: string, config: Record<string, any>): Observable<{ circadian: Record<string, any> }> {
    return this.http.patch<{ circadian: Record<string, any> }>(
      `${this.RUNTIME}/admin/agents/${agentId}/config/circadian`, config,
    );
  }

  // ─── Front-Brain Data ──────────────────────────────────────────
  getWorkingMemory(agentId: string): Observable<WorkingMemoryStatus> {
    return this.http.get<WorkingMemoryStatus>(`${this.RUNTIME}/agents/${agentId}/working-memory`);
  }

  getTheoryOfMind(agentId: string): Observable<TheoryOfMindStatus> {
    return this.http.get<TheoryOfMindStatus>(`${this.RUNTIME}/agents/${agentId}/theory-of-mind`);
  }

  getNarrativeEpisodes(agentId: string): Observable<NarrativeStatus> {
    return this.http.get<NarrativeStatus>(`${this.RUNTIME}/agents/${agentId}/narrative/episodes`);
  }

  getNetworkDynamics(agentId: string): Observable<NetworkDynamicsStatus> {
    return this.http.get<NetworkDynamicsStatus>(`${this.RUNTIME}/agents/${agentId}/network-dynamics`);
  }

  // ─── Actions ──────────────────────────────────────────────────
  forceSleep(agentId: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/sleep`, {});
  }

  // ─── Tools ────────────────────────────────────────────────────
  getToolCatalog(): Observable<any[]> {
    return this.http.get<any[]>(`${this.RUNTIME}/admin/tools/catalog`);
  }

  getToolCatalogV2(): Observable<any[]> {
    return this.http.get<any[]>(`${this.RUNTIME}/admin/tools/catalog/v2`);
  }

  getAgentTools(agentId: string): Observable<{ enabled: Array<string | { name: string; description?: string }>; version?: number }> {
    return this.http.get<{ enabled: Array<string | { name: string; description?: string }>; version?: number }>(`${this.RUNTIME}/admin/agents/${agentId}/tools`);
  }

  enableTool(agentId: string, toolName: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/tools/${toolName}/enable`, {});
  }

  disableTool(agentId: string, toolName: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/tools/${toolName}/disable`, {});
  }

  getToolOnboardingStatus(agentId: string, toolName: string): Observable<any> {
    return this.http.get(`${this.RUNTIME}/admin/agents/${agentId}/tools/${toolName}/status`);
  }

  getToolBundles(): Observable<any[]> {
    return this.http.get<any[]>(`${this.RUNTIME}/admin/tools/bundles`);
  }

  batchEnableTools(agentId: string, body: { tools?: string[]; bundle?: string }): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/tools/batch-enable`, body);
  }

  getBatchOnboardingStatus(agentId: string, batchId: string): Observable<any> {
    return this.http.get(`${this.RUNTIME}/admin/agents/${agentId}/tools/batch/${batchId}/status`);
  }

  // ─── ANS Working Memory ─────────────────────────────────────
  getAnsContext(agentId: string): Observable<{ items: any[]; total: number }> {
    return this.http.get<{ items: any[]; total: number }>(`${this.RUNTIME}/admin/agents/${agentId}/ans/context`);
  }

  deleteAnsContextItem(agentId: string, index: number): Observable<any> {
    return this.http.delete(`${this.RUNTIME}/admin/agents/${agentId}/ans/context/${index}`);
  }

  updateAnsContextItem(agentId: string, index: number, content: string): Observable<any> {
    return this.http.patch(`${this.RUNTIME}/admin/agents/${agentId}/ans/context/${index}`, { content });
  }

  updateWmInstruction(agentId: string, index: number, content: string): Observable<any> {
    return this.http.patch(`${this.RUNTIME}/agents/${agentId}/working-memory/instructions/${index}`, { content });
  }

  deleteWmInstruction(agentId: string, index: number): Observable<any> {
    return this.http.delete(`${this.RUNTIME}/agents/${agentId}/working-memory/instructions/${index}`);
  }

  submitMessageFeedback(agentId: string, data: {
    comment: string;
    messageContent: string;
    feedbackType: 'SPECIFIC' | 'GLOBAL';
    channel?: string;
    sessionKey?: string;
  }): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/feedback`, data);
  }

  // ─── Health ───────────────────────────────────────────────────
  getHealth(): Observable<any> {
    return this.http.get(`${this.RUNTIME}/health`);
  }

  // ─── File Upload / Download ──────────────────────────────────
  uploadFiles(agentId: string, files: File[]): Observable<FileAttachment[]> {
    const formData = new FormData();
    for (const f of files) {
      formData.append('files', f, f.name);
    }
    return this.http.post<FileAttachment[]>(
      `${this.RUNTIME}/agents/${agentId}/files/upload`,
      formData,
    );
  }

  getDownloadUrl(agentId: string, path: string): string {
    return `${this.RUNTIME}/agents/${agentId}/files/download?path=${encodeURIComponent(path)}`;
  }

  // ─── Skills ──────────────────────────────────────────────────
  getSkills(): Observable<any[]> {
    return this.http.get<any[]>(`${this.RUNTIME}/admin/skills`);
  }

  getSkill(name: string): Observable<any> {
    return this.http.get<any>(`${this.RUNTIME}/admin/skills/${name}`);
  }

  enableSkill(name: string): Observable<any> {
    return this.http.post<any>(`${this.RUNTIME}/admin/skills/${name}/enable`, {});
  }

  disableSkill(name: string): Observable<any> {
    return this.http.post<any>(`${this.RUNTIME}/admin/skills/${name}/disable`, {});
  }

  deleteSkill(name: string): Observable<any> {
    return this.http.delete<any>(`${this.RUNTIME}/admin/skills/${name}`);
  }

  getSkillConfig(name: string, withSchema = false, agentId?: string): Observable<any> {
    let params = new HttpParams();
    if (withSchema) params = params.set('with_schema', 'true');
    if (agentId) params = params.set('agent_id', agentId);
    return this.http.get<any>(`${this.RUNTIME}/admin/skills/${name}/config`, { params });
  }

  updateSkillConfig(name: string, config: any, agentId?: string): Observable<any> {
    let params = new HttpParams();
    if (agentId) params = params.set('agent_id', agentId);
    return this.http.patch<any>(`${this.RUNTIME}/admin/skills/${name}/config`, config, { params });
  }

  getSkillFile(name: string, path: string): Observable<{ path: string; content: string; size: number }> {
    return this.http.get<{ path: string; content: string; size: number }>(
      `${this.RUNTIME}/admin/skills/${name}/files/${path}`
    );
  }

  updateSkillFile(name: string, path: string, content: string): Observable<any> {
    return this.http.put<any>(
      `${this.RUNTIME}/admin/skills/${name}/files/${path}`,
      { content }
    );
  }

  getSkillReviews(): Observable<any[]> {
    return this.http.get<any[]>(`${this.RUNTIME}/admin/skills/reviews/list`);
  }

  approveSkillReview(reviewId: string): Observable<any> {
    return this.http.post<any>(`${this.RUNTIME}/admin/skills/reviews/${reviewId}/approve`, {});
  }

  rejectSkillReview(reviewId: string): Observable<any> {
    return this.http.post<any>(`${this.RUNTIME}/admin/skills/reviews/${reviewId}/reject`, {});
  }

  getAgentSkills(agentId: string): Observable<any[]> {
    return this.http.get<any[]>(`${this.RUNTIME}/admin/agents/${agentId}/skills`);
  }

  enableAgentSkill(agentId: string, name: string): Observable<any> {
    return this.http.post<any>(`${this.RUNTIME}/admin/agents/${agentId}/skills/${name}/enable`, {});
  }

  disableAgentSkill(agentId: string, name: string): Observable<any> {
    return this.http.post<any>(`${this.RUNTIME}/admin/agents/${agentId}/skills/${name}/disable`, {});
  }

  // ─── Relay Status ──────────────────────────────────────────────
  /** Local Python runtime: ChannelRelayClient connected to NestJS (Electron). */
  getLocalRelayStatus(agentId: string): Observable<{ online: boolean; connected?: boolean }> {
    return this.http.get<{ online: boolean; connected?: boolean }>(
      `${this.RUNTIME}/agents/${agentId}/relay-status`,
    );
  }

  getRelayStatus(agentId: string): Observable<{ online: boolean }> {
    return this.http.get<{ online: boolean }>(`${this.API}/agents/${agentId}/relay-status`);
  }

  getAllRelayStatus(): Observable<{ id: string; runtimeAgentId: string; name: string; online: boolean }[]> {
    return this.http.get<any[]>(`${this.API}/agents/relay-status`);
  }

  // ─── Soul Package & Memory Tiers ────────────────────────────────
  getMemoryTiers(agentId: string): Observable<MemoryTiers> {
    return this.http.get<MemoryTiers>(`${this.RUNTIME}/admin/agents/${agentId}/memory-tiers`);
  }

  exportSoulPackage(agentId: string, includeSessions = false): Observable<Blob> {
    let params = new HttpParams();
    if (includeSessions) params = params.set('include_sessions', 'true');
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/soul/export`, null, {
      params,
      responseType: 'blob',
    });
  }

  importSoulPackage(agentId: string, file: File): Observable<SoulImportResult> {
    const formData = new FormData();
    formData.append('file', file, file.name);
    return this.http.post<SoulImportResult>(
      `${this.RUNTIME}/admin/agents/${agentId}/soul/import`,
      formData,
    );
  }

  forkAgent(agentId: string, forkHeight: number, newAgentName?: string): Observable<ForkResult> {
    const body: Record<string, any> = { fork_height: forkHeight };
    if (newAgentName) body['new_agent_name'] = newAgentName;
    return this.http.post<ForkResult>(
      `${this.RUNTIME}/admin/agents/${agentId}/soul/fork`,
      body,
    );
  }

  // ─── Snapshots ────────────────────────────────────────────────
  createSnapshot(agentId: string, label?: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/soul/snapshot`, { label: label || '' });
  }

  listSnapshots(agentId: string): Observable<{ snapshots: any[] }> {
    return this.http.get<{ snapshots: any[] }>(`${this.RUNTIME}/admin/agents/${agentId}/soul/snapshots`);
  }

  restoreSnapshot(agentId: string, snapshotFile: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/admin/agents/${agentId}/soul/snapshot/restore`, { file: snapshotFile });
  }

  // ─── Todo List ────────────────────────────────────────────────
  getTodoLists(agentId: string): Observable<{ lists: any[] }> {
    return this.http.get<{ lists: any[] }>(`${this.RUNTIME}/skills/todo-list/${agentId}/lists`);
  }

  createTodoList(agentId: string, body: { name: string; icon?: string; description?: string; color?: string }): Observable<any> {
    return this.http.post(`${this.RUNTIME}/skills/todo-list/${agentId}/lists`, body);
  }

  getTodoItems(agentId: string, params?: { status?: string; list_id?: string }): Observable<{ items: any[] }> {
    let httpParams = new HttpParams();
    if (params?.status) httpParams = httpParams.set('status', params.status);
    if (params?.list_id) httpParams = httpParams.set('list_id', params.list_id);
    return this.http.get<{ items: any[] }>(`${this.RUNTIME}/skills/todo-list/${agentId}/items`, { params: httpParams });
  }

  createTodoItem(agentId: string, body: any): Observable<any> {
    return this.http.post(`${this.RUNTIME}/skills/todo-list/${agentId}/items`, body);
  }

  updateTodoItem(agentId: string, itemId: string, body: any): Observable<any> {
    return this.http.put(`${this.RUNTIME}/skills/todo-list/${agentId}/items/${itemId}`, body);
  }

  deleteTodoItem(agentId: string, itemId: string): Observable<any> {
    return this.http.delete(`${this.RUNTIME}/skills/todo-list/${agentId}/items/${itemId}`);
  }

  getTodoPlan(agentId: string, itemId: string): Observable<any> {
    return this.http.get(`${this.RUNTIME}/skills/todo-list/${agentId}/items/${itemId}/plan`);
  }

  // ─── Job & Trust ───────────────────────────────────────────────
  getJob(agentId: string): Observable<JobDocument> {
    return this.http.get<JobDocument>(`${this.RUNTIME}/agents/${agentId}/job`);
  }

  patchJob(agentId: string, body: Partial<JobDocument>): Observable<JobDocument> {
    return this.http.patch<JobDocument>(`${this.RUNTIME}/agents/${agentId}/job`, body);
  }

  getTrust(agentId: string): Observable<TrustDocument> {
    return this.http.get<TrustDocument>(`${this.RUNTIME}/agents/${agentId}/trust`);
  }

  patchTrust(agentId: string, body: Partial<TrustDocument>): Observable<TrustDocument> {
    return this.http.patch<TrustDocument>(`${this.RUNTIME}/agents/${agentId}/trust`, body);
  }

  // ─── Squads ────────────────────────────────────────────────────
  listSquads(callerAgentId?: string): Observable<{ squads: Squad[] }> {
    let params = new HttpParams();
    if (callerAgentId) {
      params = params.set('caller_agent_id', callerAgentId);
    }
    return this.http.get<{ squads: Squad[] }>(`${this.RUNTIME}/api/squads`, { params });
  }

  getSquadKanban(squadId: string, callerAgentId: string): Observable<SquadKanbanBoard> {
    const params = new HttpParams().set('caller_agent_id', callerAgentId);
    return this.http.get<SquadKanbanBoard>(
      `${this.RUNTIME}/api/squads/${squadId}/kanban`,
      { params },
    );
  }

  createSquad(body: SquadCreate): Observable<Squad> {
    return this.http.post<Squad>(`${this.RUNTIME}/api/squads`, body);
  }

  getSquad(squadId: string): Observable<Squad> {
    return this.http.get<Squad>(`${this.RUNTIME}/api/squads/${squadId}`);
  }

  updateSquad(
    squadId: string,
    body: Partial<SquadCreate> & {
      checkback_enabled?: boolean;
      checkback_interval_seconds?: number;
      proposal_sla_seconds?: number;
    },
    callerAgentId?: string,
  ): Observable<Squad> {
    let params = new HttpParams();
    if (callerAgentId) {
      params = params.set('caller_agent_id', callerAgentId);
    }
    return this.http.patch<Squad>(`${this.RUNTIME}/api/squads/${squadId}`, body, { params });
  }

  deleteSquad(squadId: string, callerAgentId?: string): Observable<{ deleted: string }> {
    let params = new HttpParams();
    if (callerAgentId) {
      params = params.set('caller_agent_id', callerAgentId);
    }
    return this.http.delete<{ deleted: string }>(
      `${this.RUNTIME}/api/squads/${squadId}`,
      { params },
    );
  }

  getSquadForAgent(agentId: string): Observable<{ squad: Squad | null; is_lead: boolean }> {
    return this.http.get<{ squad: Squad | null; is_lead: boolean }>(
      `${this.RUNTIME}/api/squads/by-agent/${agentId}`,
    );
  }

  // ─── Teams ─────────────────────────────────────────────────────
  getTeams(agentId: string, includeCompleted = false): Observable<{ teams: any[] }> {
    let params = new HttpParams();
    if (includeCompleted) params = params.set('include_completed', 'true');
    return this.http.get<{ teams: any[] }>(`${this.RUNTIME}/api/agents/${agentId}/teams`, { params });
  }

  createTeam(agentId: string, body: { plan_id: string; wave: number; name: string; mission?: string; briefing?: string }): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams`, body);
  }

  getTeam(agentId: string, teamId: string): Observable<{ team: any }> {
    return this.http.get<{ team: any }>(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}`);
  }

  advanceTeam(agentId: string, teamId: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/advance`, {});
  }

  pauseTeam(agentId: string, teamId: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/pause`, {});
  }

  resumeTeam(agentId: string, teamId: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/resume`, {});
  }

  disbandTeam(agentId: string, teamId: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/disband`, {});
  }

  hintTeamMember(agentId: string, teamId: string, memberIdx: number, message: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/members/${memberIdx}/hint`, { message });
  }

  briefTeam(agentId: string, teamId: string, content: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/brief`, { content });
  }

  sendCommand(agentId: string, message: string, context: any = {}): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/command`, { message, context });
  }

  getTimeline(agentId: string, planId: string): Observable<any> {
    return this.http.get(`${this.RUNTIME}/api/agents/${agentId}/projects/${planId}/timeline`);
  }

  skipWave(agentId: string, teamId: string): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/teams/${teamId}/skip`, {});
  }

  forceStartWave(agentId: string, planId: string, waveIndex: number): Observable<any> {
    return this.http.post(`${this.RUNTIME}/api/agents/${agentId}/projects/${planId}/force-start/${waveIndex}`, {});
  }

  getFileTree(path: string, depth = 3): Observable<any> {
    let params = new HttpParams().set('path', path).set('depth', depth.toString());
    return this.http.get(`${this.RUNTIME}/fs/tree`, { params });
  }

  readFile(path: string, offset = 0, limit = 0): Observable<any> {
    let params = new HttpParams().set('path', path);
    if (offset) params = params.set('offset', offset.toString());
    if (limit) params = params.set('limit', limit.toString());
    return this.http.get(`${this.RUNTIME}/fs/read`, { params });
  }

  listSessions(agentId: string): Observable<any> {
    const url = this.platform.isElectron
      ? `${this.RUNTIME}/sessions/${agentId}`
      : `${this.API}/agents/${agentId}/sessions`;
    return this.http.get(url);
  }

  getSessionHistory(agentId: string, sessionKey: string): Observable<any> {
    const encoded = encodeURIComponent(sessionKey);
    const url = this.platform.isElectron
      ? `${this.RUNTIME}/sessions/${agentId}/${encoded}`
      : `${this.API}/agents/${agentId}/sessions/${encoded}`;
    return this.http.get(url);
  }

  // ─── Visual Cortex ──────────────────────────────────────────────
  getVisualCortexBuffer(agentId: string, opts?: { channel?: string; limit?: number }): Observable<any> {
    let params = new HttpParams();
    if (opts?.channel) params = params.set('channel', opts.channel);
    if (opts?.limit) params = params.set('limit', opts.limit.toString());
    return this.http.get<any>(`${this.RUNTIME}/admin/agents/${agentId}/visual-cortex/buffer`, { params });
  }

  // ─── Transcription ────────────────────────────────────────────
  transcribe(audioBlob: Blob, filename = 'recording.webm'): Observable<{ text: string; language: string; duration: number }> {
    const formData = new FormData();
    formData.append('audio', audioBlob, filename);
    return this.http.post<{ text: string; language: string; duration: number }>(`${this.RUNTIME}/transcribe`, formData);
  }

  // ─── Google Workspace OAuth ─────────────────────────────
  connectGoogleWorkspace(agentId: string): Observable<{ auth_url?: string; connected?: boolean; email?: string; message?: string }> {
    return this.http.post<any>(`${this.RUNTIME}/skills/google-workspace/connect/${agentId}`, {});
  }

  getGoogleWorkspaceStatus(agentId: string): Observable<{ connected: boolean; email?: string; enabled?: boolean }> {
    return this.http.get<any>(`${this.RUNTIME}/skills/google-workspace/status/${agentId}`);
  }

  disconnectGoogleWorkspace(agentId: string): Observable<{ disconnected: boolean }> {
    return this.http.post<any>(`${this.RUNTIME}/skills/google-workspace/disconnect/${agentId}`, {});
  }

  // ─── Platform integrations (NestJS) ───────────────────────────
  getPlatformCapabilities(): Observable<import('../models/platform-capabilities.model').PlatformCapabilities> {
    return this.http.get<any>(`${this.API}/cloud/platform-capabilities`);
  }

  getResendProviderStatus(): Observable<{ configured: boolean; inboundDomain: string | null }> {
    return this.http.get<any>(`${this.API}/cloud/providers/resend`);
  }

  saveResendProvider(apiKey: string, inboundDomain: string): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.API}/cloud/providers/resend`, {
      apiKey,
      inboundDomain,
    });
  }

  clearResendProvider(): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.API}/cloud/providers/resend`);
  }

  setCloudInferenceProviderKey(
    provider: string,
    apiKey: string,
  ): Observable<{ ok: boolean; provider: string }> {
    return this.http.put<{ ok: boolean; provider: string }>(
      `${this.API}/cloud/providers/inference/${encodeURIComponent(provider)}`,
      { apiKey },
    );
  }

  // ─── Babo Cloud billing ───────────────────────────────────────
  getCloudSubscription(): Observable<import('../models/cloud-subscription.model').CloudSubscriptionView> {
    return this.http.get<any>(`${this.API}/cloud/subscription`);
  }

  getCloudUsage(limit = 25): Observable<import('../models/cloud-subscription.model').CloudUsageResponse> {
    const params = new HttpParams().set('limit', String(limit));
    return this.http.get<any>(`${this.API}/cloud/usage`, { params });
  }

  syncBillingSubscription(): Observable<import('../models/cloud-subscription.model').CloudSubscriptionView> {
    return this.http.post<any>(`${this.API}/billing/sync`, {});
  }

  createBillingCheckout(
    returnUrl: string,
    flow?: 'setup' | 'settings',
  ): Observable<{ url: string }> {
    return this.http.post<{ url: string }>(`${this.API}/billing/checkout`, {
      returnUrl,
      flow,
    });
  }

  createBillingPortal(
    returnUrl: string,
    flow?: 'setup' | 'settings',
  ): Observable<{ url: string }> {
    return this.http.post<{ url: string }>(`${this.API}/billing/portal`, {
      returnUrl,
      flow,
    });
  }

  updateBillingSpendCap(capCents: number | null): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.API}/billing/spend-cap`, {
      capCents,
    });
  }

  setBillingOnDemand(enabled: boolean): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.API}/billing/on-demand`, {
      enabled,
    });
  }

  // ─── Helpers ─────────────────────────────────────────────────
  /** Coerce unix seconds, ISO strings, or Date-like values to ISO UTC. */
  private normalizeTimestamp(value: unknown): string {
    if (value == null || value === '') return '';
    if (typeof value === 'number' && Number.isFinite(value)) {
      const ms = value < 1e12 ? value * 1000 : value;
      return new Date(ms).toISOString();
    }
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (/^\d+(\.\d+)?$/.test(trimmed)) {
        const ms = Number(trimmed) < 1e12 ? Number(trimmed) * 1000 : Number(trimmed);
        return new Date(ms).toISOString();
      }
      const ts = this.parseUtcTimestamp(trimmed);
      return ts != null ? new Date(ts).toISOString() : trimmed;
    }
    return String(value);
  }

  /** Naive ISO datetimes from Python utcnow() are UTC, not local. */
  private parseUtcTimestamp(value: string): number | null {
    const naiveIso =
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(value);
    const normalized = naiveIso ? `${value}Z` : value;
    const ts = Date.parse(normalized);
    return Number.isNaN(ts) ? null : ts;
  }

  /**
   * Normalize agent payloads from the local Python runtime (snake_case)
   * to the Angular Agent model (camelCase). Handles both NestJS and
   * local runtime response shapes gracefully.
   */
  private normalizeAgent(raw: any): Agent {
    const runtimeAgentId =
      raw.runtimeAgentId || raw.agent_id || '';
    const runtime = this.extractRuntimeStatus(raw);
    const agentStatus =
      raw.status || runtime?.status || 'offline';
    if (runtime && !runtime.status) {
      runtime.status = agentStatus;
    }
    return {
      id: raw.id || raw.agent_id || '',
      userId: raw.userId || raw.user_id || '',
      runtimeAgentId,
      name: raw.name || null,
      genesisVersion: raw.genesisVersion || raw.genesis_version || '',
      status: agentStatus,
      createdAt: this.normalizeTimestamp(raw.createdAt ?? raw.created_at),
      runtime,
      userPaused: raw.userPaused ?? raw.user_paused ?? false,
      jobTitle: raw.jobTitle ?? raw.job_title ?? runtime?.job_title,
      squadId: raw.squadId ?? raw.squad_id ?? runtime?.squad_id,
      squadName: raw.squadName ?? raw.squad_name ?? runtime?.squad_name,
      isSquadLead: raw.isSquadLead ?? raw.is_squad_lead ?? runtime?.is_squad_lead,
    };
  }

  /** Merge nested or flat Python runtime fields into AgentRuntimeStatus. */
  private extractRuntimeStatus(raw: any): AgentRuntimeStatus | undefined {
    const nested = raw.runtime;
    const runtime: AgentRuntimeStatus =
      nested && typeof nested === 'object' ? { ...nested } : {};

    const flatKeys = [
      'agent_id', 'agent_name', 'status', 'initialized', 'turn_count', 'sleep_count',
      'facts_in_memory', 'fact_count', 'hormones', 'ans', 'thalamus', 'thalamus_bands',
      'in_vram', 'heartbeat', 'working_memory', 'narrative', 'narrative_self',
      'theory_of_mind', 'predictive', 'predictive_processing', 'network_dynamics',
      'last_interaction', 'orchestrator_model', 'delegate_model', 'activity',
      'consciousness',
      'job_title', 'squad_id', 'squad_name', 'is_squad_lead',
    ] as const;

    for (const key of flatKeys) {
      if (raw[key] !== undefined && runtime[key] === undefined) {
        runtime[key] = raw[key];
      }
    }

    if (runtime.last_interaction) {
      runtime.last_interaction = this.normalizeTimestamp(runtime.last_interaction);
    }

    return Object.keys(runtime).length > 0 ? runtime : undefined;
  }
}

export interface JobDocument {
  title?: string;
  mission?: string;
  persona?: string;
  playbook?: string;
  default_profile?: string;
  in_scope?: string[];
  out_of_scope?: string[];
  refusal_template?: string;
}

export interface TrustDocument {
  tools_allow?: string[];
  tools_deny?: string[];
  action_classes_allow?: string[];
  action_classes_deny?: string[];
  channel_overlays?: ChannelTrustOverlay[];
}

export interface SquadCreate {
  name: string;
  lead_agent_id: string;
  member_agent_ids?: string[];
}

export interface Squad {
  id: string;
  name: string;
  lead_agent_id: string;
  member_agent_ids: string[];
  inbox?: SquadInboxItem[];
  escalations?: { member_agent_id: string; reason: string; status: string }[];
  job_titles?: Record<string, string>;
  paused?: boolean;
  checkback_enabled?: boolean;
  checkback_interval_seconds?: number;
  proposal_sla_seconds?: number;
  last_checkback_at?: number;
}

export interface SquadInboxItem {
  id: string;
  title: string;
  status: string;
  suggested_assignee_id?: string;
  assignee_id?: string;
  created_at?: number;
}

export interface SquadKanbanBoard {
  squad_id: string;
  inbox: {
    proposed: SquadInboxItem[];
    approved: SquadInboxItem[];
    rejected: SquadInboxItem[];
  };
  member_todos: Record<string, SquadMemberTodo[]>;
  open_escalations: { member_agent_id: string; reason: string; status: string }[];
  job_titles?: Record<string, string>;
}

export interface SquadMemberTodo {
  id: string;
  title: string;
  status: string;
  squad_id?: string;
}

export interface ChannelTrustOverlay {
  channel_key: string;
  profile_cap?: string;
  tools_allow?: string[];
  tools_deny?: string[];
  public_channel?: boolean;
}
