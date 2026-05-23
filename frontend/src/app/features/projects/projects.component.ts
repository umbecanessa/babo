import { Component, OnInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { ProjectService } from './project.service';
import { TeamsPanelComponent } from './teams-panel/teams-panel.component';
import { BoardPanelComponent } from './board-panel/board-panel.component';
import { TimelinePanelComponent } from './timeline-panel/timeline-panel.component';
import { ActivityPanelComponent } from './activity-panel/activity-panel.component';
import { CommandBarComponent } from './command-bar/command-bar.component';
import { ChatSidebarComponent } from './chat-sidebar/chat-sidebar.component';
import { FilesPanelComponent } from './files-panel/files-panel.component';

type PanelTab = 'overview' | 'board' | 'timeline' | 'files';

@Component({
  selector: 'app-projects',
  standalone: true,
  imports: [
    CommonModule,
    TeamsPanelComponent,
    BoardPanelComponent,
    TimelinePanelComponent,
    ActivityPanelComponent,
    CommandBarComponent,
    ChatSidebarComponent,
    FilesPanelComponent,
  ],
  templateUrl: './projects.component.html',
  styleUrl: './projects.component.scss',
})
export class ProjectsComponent implements OnInit, OnDestroy {
  agentId = '';
  activeTab = signal<PanelTab>('overview');
  chatOpen = signal(false);

  orchActiveCount = computed(() => this.svc.activeTeams().length);
  orchTotalMembers = computed(() => {
    const teams = this.svc.activeTeams();
    return teams.reduce((sum, t) => sum + (t.members?.length ?? 0), 0);
  });
  orchDoneMembers = computed(() => {
    const teams = this.svc.activeTeams();
    return teams.reduce((sum, t) => sum + (t.members?.filter(m => m.status === 'done').length ?? 0), 0);
  });
  orchFailedMembers = computed(() => {
    const teams = this.svc.activeTeams();
    return teams.reduce((sum, t) => sum + (t.members?.filter(m => m.status === 'failed').length ?? 0), 0);
  });
  orchPlanProgress = computed(() => {
    const plans = this.svc.plansByTodoId();
    const entries = Object.values(plans);
    if (entries.length === 0) return 0;
    const totalSteps = entries.reduce((s, p) => s + (p.steps?.length ?? 0), 0);
    if (totalSteps === 0) return 0;
    const doneSteps = entries.reduce((s, p) => s + (p.steps?.filter(st => st.status === 'done').length ?? 0), 0);
    return Math.round((doneSteps / totalSteps) * 100);
  });
  hasOrchData = computed(() =>
    this.svc.activeTeams().length > 0 || this.orchPlanProgress() > 0
  );

  constructor(
    private route: ActivatedRoute,
    public svc: ProjectService,
  ) {}

  ngOnInit(): void {
    this.agentId = this.route.snapshot.params['agentId'];
    this.svc.init(this.agentId);
  }

  ngOnDestroy(): void {
    this.svc.destroy();
  }

  setTab(tab: PanelTab): void {
    this.activeTab.set(tab);
  }

  toggleChat(): void {
    this.chatOpen.update(v => !v);
  }

  onCommand(message: string): void {
    this.svc.sendCommand(message);
  }
}
