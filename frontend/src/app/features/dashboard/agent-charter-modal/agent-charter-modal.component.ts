import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ApiService,
  JobDocument,
  TrustDocument,
  ChannelTrustOverlay,
} from '../../../core/services/api.service';

export type CharterTab = 'job' | 'trust';

@Component({
  selector: 'app-agent-charter-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './agent-charter-modal.component.html',
  styleUrl: './agent-charter-modal.component.scss',
})
export class AgentCharterModalComponent implements OnChanges {
  @Input({ required: true }) agentId = '';
  @Input() agentLabel = '';
  @Input() visible = false;
  @Input() initialTab: CharterTab = 'job';

  @Output() dismiss = new EventEmitter<boolean>();

  activeTab = signal<CharterTab>('job');
  saving = signal(false);
  error = signal('');
  loaded = signal(false);

  jobTitle = '';
  jobMission = '';
  jobPersona = '';
  jobPlaybook = '';
  jobDefaultProfile = '';
  jobInScope = '';
  jobOutOfScope = '';
  jobRefusalTemplate = '';
  jobStrategicPriorities = '';
  jobBackgroundEnabled = false;
  jobBackgroundIntervalSeconds = 3600;

  toolsAllow = '';
  toolsDeny = '';
  actionClassesDeny = '';
  channelOverlays: ChannelTrustOverlay[] = [];

  profileCapOptions = [
    '',
    'conversational',
    'solo_structured',
    'orchestrated',
    'squad_lead',
  ];

  constructor(private api: ApiService) {}

  ngOnChanges(changes: SimpleChanges): void {
    const opened = changes['visible']?.currentValue === true;
    const agentChanged = changes['agentId'] && !changes['agentId'].firstChange;
    if (this.visible && this.agentId && (opened || agentChanged)) {
      this.activeTab.set(this.initialTab);
      this.load();
    }
  }

  load(): void {
    this.loaded.set(false);
    this.error.set('');
    this.api.getJob(this.agentId).subscribe({
      next: job => this.applyJob(job),
      error: err => this.error.set(this.errMsg(err)),
    });
    this.api.getTrust(this.agentId).subscribe({
      next: trust => this.applyTrust(trust),
      error: err => this.error.set(this.errMsg(err)),
    });
  }

  private applyJob(job: JobDocument): void {
    this.jobTitle = job.title || '';
    this.jobMission = job.mission || '';
    this.jobPersona = job.persona || '';
    this.jobPlaybook = job.playbook || '';
    this.jobDefaultProfile = job.default_profile || '';
    this.jobInScope = (job.in_scope || []).join('\n');
    this.jobOutOfScope = (job.out_of_scope || []).join('\n');
    this.jobRefusalTemplate = job.refusal_template || '';
    this.jobStrategicPriorities = (job.strategic_priorities || []).join('\n');
    this.jobBackgroundEnabled = !!job.background_enabled;
    this.jobBackgroundIntervalSeconds =
      job.background_interval_seconds && job.background_interval_seconds > 0
        ? job.background_interval_seconds
        : 3600;
    this.loaded.set(true);
  }

  private applyTrust(trust: TrustDocument): void {
    this.toolsAllow = (trust.tools_allow || []).join('\n');
    this.toolsDeny = (trust.tools_deny || []).join('\n');
    this.actionClassesDeny = (trust.action_classes_deny || []).join('\n');
    this.channelOverlays = (trust.channel_overlays || []).map(o => ({ ...o }));
    this.loaded.set(true);
  }

  setTab(tab: CharterTab): void {
    this.activeTab.set(tab);
  }

  addChannelOverlay(): void {
    this.channelOverlays = [
      ...this.channelOverlays,
      { channel_key: '', profile_cap: '', tools_deny: [], public_channel: false },
    ];
  }

  removeChannelOverlay(index: number): void {
    this.channelOverlays = this.channelOverlays.filter((_, i) => i !== index);
  }

  overlayToolsDenyText(ov: ChannelTrustOverlay): string {
    return (ov.tools_deny || []).join('\n');
  }

  setOverlayToolsDeny(ov: ChannelTrustOverlay, text: string): void {
    ov.tools_deny = this.linesToList(text);
  }

  saveJob(): void {
    if (!this.agentId) return;
    this.saving.set(true);
    this.error.set('');
    this.api.patchJob(this.agentId, this.buildJobBody()).subscribe({
      next: () => {
        this.saving.set(false);
        this.dismiss.emit(true);
      },
      error: err => {
        this.saving.set(false);
        this.error.set(this.errMsg(err));
      },
    });
  }

  saveTrust(): void {
    if (!this.agentId) return;
    this.saving.set(true);
    this.error.set('');
    this.api.patchTrust(this.agentId, this.buildTrustBody()).subscribe({
      next: () => {
        this.saving.set(false);
        this.dismiss.emit(true);
      },
      error: err => {
        this.saving.set(false);
        this.error.set(this.errMsg(err));
      },
    });
  }

  private buildJobBody(): Partial<JobDocument> {
    return {
      title: this.jobTitle.trim(),
      mission: this.jobMission.trim(),
      persona: this.jobPersona.trim(),
      playbook: this.jobPlaybook.trim(),
      default_profile: this.jobDefaultProfile.trim(),
      in_scope: this.linesToList(this.jobInScope),
      out_of_scope: this.linesToList(this.jobOutOfScope),
      refusal_template: this.jobRefusalTemplate.trim(),
      strategic_priorities: this.linesToList(this.jobStrategicPriorities),
      background_enabled: this.jobBackgroundEnabled,
      background_interval_seconds: this.jobBackgroundEnabled
        ? Math.max(300, Math.floor(this.jobBackgroundIntervalSeconds || 3600))
        : 0,
    };
  }

  private buildTrustBody(): Partial<TrustDocument> {
    return {
      tools_allow: this.linesToList(this.toolsAllow),
      tools_deny: this.linesToList(this.toolsDeny),
      action_classes_deny: this.linesToList(this.actionClassesDeny),
      channel_overlays: this.channelOverlays
        .filter(o => (o.channel_key || '').trim())
        .map(o => ({
          channel_key: o.channel_key.trim(),
          profile_cap: (o.profile_cap || '').trim(),
          tools_allow: o.tools_allow || [],
          tools_deny: o.tools_deny || [],
          public_channel: !!o.public_channel,
        })),
    };
  }

  overlayToolsAllowText(ov: ChannelTrustOverlay): string {
    return (ov.tools_allow || []).join('\n');
  }

  setOverlayToolsAllow(ov: ChannelTrustOverlay, text: string): void {
    ov.tools_allow = this.linesToList(text);
  }

  cancel(): void {
    this.dismiss.emit(false);
  }

  private linesToList(text: string): string[] {
    return text
      .split(/[\n,]/)
      .map(s => s.trim())
      .filter(Boolean);
  }

  private errMsg(err: unknown): string {
    const e = err as { error?: { detail?: string }; message?: string };
    return e?.error?.detail || e?.message || 'Request failed';
  }
}
