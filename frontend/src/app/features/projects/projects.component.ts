import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
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

type PanelTab = 'overview' | 'board' | 'files';

@Component({
  selector: 'app-projects',
  standalone: true,
  imports: [
    CommonModule,
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
    const entries = Object.values(this.svc.plansByTodoId()) as PlanSummary[];
    let done = 0;
    let skipped = 0;
    let total = 0;
    for (const plan of entries) {
      for (const step of plan.steps ?? []) {
        total++;
        if (step.status === 'done') {
          done++;
        } else if (step.status === 'skipped') {
          skipped++;
        }
      }
    }
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    return { done, skipped, total, pct };
  });

  orchPlanProgress = computed(() => this.orchPlanStats().pct);

  hasOrchData = computed(
    () =>
      this.svc.activeTeams().length > 0
      || this.orchPlanStats().total > 0,
  );

  orchUnlaunchedCount = computed(() => this.svc.unlaunchedTeams().length);

  constructor(
    private route: ActivatedRoute,
    public svc: ProjectService,
    readonly runView: RunViewService,
  ) {}

  ngOnInit(): void {
    this.agentId = this.route.snapshot.params['agentId'];
    this.svc.init(this.agentId);
    this.route.queryParamMap.subscribe((params) => {
      const tab = params.get('tab');
      if (tab === 'overview' || tab === 'board' || tab === 'files') {
        this.activeTab.set(tab);
      }
      const path = params.get('path');
      if (path) {
        this.pendingFilePath.set(path);
      }
    });
  }

  ngOnDestroy(): void {
    this.svc.destroy();
  }

  setTab(tab: PanelTab): void {
    this.activeTab.set(tab);
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
