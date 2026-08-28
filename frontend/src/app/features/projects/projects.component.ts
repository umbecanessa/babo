import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';
import { ProjectService } from './project.service';
import { TeamsPanelComponent } from './teams-panel/teams-panel.component';
import { BoardPanelComponent } from './board-panel/board-panel.component';
import { ActivityPanelComponent } from './activity-panel/activity-panel.component';
import { CommandBarComponent } from './command-bar/command-bar.component';
import { ChatSidebarComponent } from './chat-sidebar/chat-sidebar.component';
import { WorkspaceComponent } from './workspace/workspace.component';
import { RunPanelComponent } from '../chat/run-panel/run-panel.component';
import { OverviewBoardStripComponent } from './overview-board-strip/overview-board-strip.component';
import { RunViewService } from '../../core/services/run-view.service';
import { PlanSummary } from './project.models';
import { WorkspaceTerminalTabsService } from '../../core/services/workspace-terminal-tabs.service';

type PanelTab = 'overview' | 'board' | 'files';

@Component({
  selector: 'app-projects',
  standalone: true,
  imports: [
    CommonModule,
    TranslateModule,
    TeamsPanelComponent,
    BoardPanelComponent,
    ActivityPanelComponent,
    CommandBarComponent,
    ChatSidebarComponent,
    WorkspaceComponent,
    RunPanelComponent,
    OverviewBoardStripComponent,
  ],
  templateUrl: './projects.component.html',
  styleUrl: './projects.component.scss',
})
export class ProjectsComponent implements OnInit, OnDestroy {
  agentId = '';
  activeTab = signal<PanelTab>('overview');
  chatOpen = signal(false);
  pendingFilePath = signal('');

  orchActiveCount = computed(() => this.svc.activeTeams().length);
  orchTotalMembers = computed(() => {
    const teams = this.svc.activeTeams();
    return teams.reduce((sum, t) => sum + (t.members?.length ?? 0), 0);
  });
  orchDoneMembers = computed(() => {
    const teams = this.svc.activeTeams();
    return teams.reduce(
      (sum, t) => sum + (t.members?.filter((m) => m.status === 'done').length ?? 0),
      0,
    );
  });
  orchFailedMembers = computed(() => {
    const teams = this.svc.activeTeams();
    return teams.reduce(
      (sum, t) => sum + (t.members?.filter((m) => m.status === 'failed').length ?? 0),
      0,
    );
  });

  /** Plan steps: done only for %; skipped shown separately (not counted as complete). */
  orchPlanStats = computed(() => {
    const items = this.svc.items();
    const plans = this.svc.plansByTodoId();
    const primary = items.find(
      i =>
        !!i.plan_id
        && i.status !== 'done'
        && !i.tags?.includes('team')
        && !i.tags?.includes('team-member'),
    );
    let plan = primary ? plans[primary.id] : undefined;
    if (!plan?.steps?.length) {
      const candidates = Object.values(plans).filter(p => (p.steps?.length ?? 0) > 0);
      plan = candidates.sort((a, b) => (b.steps?.length ?? 0) - (a.steps?.length ?? 0))[0];
    }
    let done = 0;
    let skipped = 0;
    let total = 0;
    for (const step of plan?.steps ?? []) {
      total++;
      if (step.status === 'done') done++;
      else if (step.status === 'skipped') skipped++;
    }
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    return { done, skipped, total, pct, planStatus: plan?.status ?? '' };
  });

  orchPlanProgress = computed(() => this.orchPlanStats().pct);

  hasOrchData = computed(
    () =>
      this.svc.activeTeams().length > 0
      || this.orchPlanStats().total > 0
      || this.runView.runningDelegateCount() > 0,
  );

  orchLiveDelegateCount = computed(() => this.runView.runningDelegateCount());

  orchUnlaunchedCount = computed(() => this.svc.unlaunchedTeams().length);

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    public svc: ProjectService,
    readonly runView: RunViewService,
    private terminalTabs: WorkspaceTerminalTabsService,
  ) {}

  ngOnInit(): void {
    this.agentId = this.route.snapshot.params['agentId'];
    this.svc.init(this.agentId);
    this.route.paramMap.subscribe((params) => {
      const nextId = params.get('agentId') || '';
      if (nextId && nextId !== this.agentId) {
        this.agentId = nextId;
        this.svc.destroy();
        this.svc.init(this.agentId);
      }
    });
    this.route.queryParamMap.subscribe((params) => {
      const tab = params.get('tab');
      if (tab === 'overview' || tab === 'board' || tab === 'files') {
        this.activeTab.set(tab);
      }
      const path = params.get('path');
      if (path) {
        this.pendingFilePath.set(path);
      } else {
        this.pendingFilePath.set('');
      }
      if (params.get('terminal') === 'open' && this.agentId) {
        this.terminalTabs.openPanel(this.agentId);
      }
    });
  }

  ngOnDestroy(): void {
    this.terminalTabs.clearAgent(this.agentId);
    this.svc.destroy();
  }

  setTab(tab: PanelTab): void {
    this.activeTab.set(tab);
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { tab },
      queryParamsHandling: 'merge',
    });
  }

  openBoardTab(): void {
    this.setTab('board');
  }

  toggleChat(): void {
    this.chatOpen.update((v) => !v);
  }

  onCommand(message: string): void {
    this.svc.sendCommand(message);
  }
}
