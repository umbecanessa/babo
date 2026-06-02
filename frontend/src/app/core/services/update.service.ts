import { Injectable, signal, computed, NgZone } from '@angular/core';
import { PlatformService } from './platform.service';

function nls(): any {
  return (window as any).nls;
}

export interface UpdateInfo {
  version: string;
  releaseNotes: string | null;
  releaseDate: string | null;
}

export interface DownloadProgress {
  percent: number;
  bytesPerSecond: number;
  transferred: number;
  total: number;
}

export type UpdateState = 'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'installing' | 'error';

@Injectable({ providedIn: 'root' })
export class UpdateService {
  readonly state = signal<UpdateState>('idle');
  readonly updateInfo = signal<UpdateInfo | null>(null);
  readonly progress = signal<DownloadProgress | null>(null);
  readonly errorMessage = signal<string | null>(null);
  readonly modalDismissedVersion = signal<string | null>(null);

  readonly updateAvailable = computed(() => {
    const s = this.state();
    return s === 'available' || s === 'downloading' || s === 'downloaded' || s === 'error';
  });
  readonly showModal = computed(() => {
    const info = this.updateInfo();
    const dismissed = this.modalDismissedVersion();
    return this.state() === 'available' && info != null && info.version !== dismissed;
  });

  constructor(
    private platform: PlatformService,
    private zone: NgZone,
  ) {
    if (this.platform.isElectron && nls()?.update) {
      this.bindEvents();
      this.pollStatus();
    }
  }

  async check(): Promise<void> {
    if (!nls()?.update) return;
    await nls().update.check();
  }

  async download(): Promise<void> {
    if (!nls()?.update) return;
    this.state.set('downloading');
    await nls().update.download();
  }

  async install(): Promise<void> {
    if (!nls()?.update) return;
    this.state.set('installing');
    await nls().update.install();
  }

  async snooze(durationMs: number): Promise<void> {
    if (!nls()?.update) return;
    await nls().update.snooze(durationMs);
    this.dismissModal();
  }

  dismissModal(): void {
    const info = this.updateInfo();
    if (info) {
      this.modalDismissedVersion.set(info.version);
    }
  }

  private bindEvents(): void {
    const api = nls();

    api.on('update:available', (data: any) => {
      this.zone.run(() => {
        this.state.set('available');
        this.updateInfo.set({
          version: data.version,
          releaseNotes: data.releaseNotes ?? null,
          releaseDate: data.releaseDate ?? null,
        });
      });
    });

    api.on('update:not-available', () => {
      this.zone.run(() => {
        this.state.set('idle');
      });
    });

    api.on('update:download-progress', (data: any) => {
      this.zone.run(() => {
        this.state.set('downloading');
        this.progress.set({
          percent: data.percent ?? 0,
          bytesPerSecond: data.bytesPerSecond ?? 0,
          transferred: data.transferred ?? 0,
          total: data.total ?? 0,
        });
      });
    });

    api.on('update:downloaded', (data: any) => {
      this.zone.run(() => {
        this.state.set('downloaded');
        if (data?.version) {
          this.updateInfo.update(prev => prev ? { ...prev, version: data.version } : prev);
        }
      });
    });

    api.on('update:error', (data: any) => {
      this.zone.run(() => {
        this.state.set('error');
        this.errorMessage.set(data?.message ?? 'Update failed');
      });
    });

    api.on('update:installing', () => {
      this.zone.run(() => {
        this.state.set('installing');
      });
    });
  }

  private async pollStatus(): Promise<void> {
    try {
      const status = await nls().update.getStatus();
      if (status?.state === 'available' && status.version) {
        this.state.set('available');
        this.updateInfo.set({
          version: status.version,
          releaseNotes: status.releaseNotes ?? null,
          releaseDate: status.releaseDate ?? null,
        });
      } else if (status?.state === 'downloaded') {
        this.state.set('downloaded');
      }
    } catch {
      // Non-critical on init
    }
  }
}
