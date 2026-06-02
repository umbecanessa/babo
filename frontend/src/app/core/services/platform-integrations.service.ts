import { Injectable, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { PlatformService } from './platform.service';
import {
  type BackendChoiceId,
  matchBackendChoice,
  normalizeNestjsUrl,
} from '../../features/setup/setup-backend.util';
import type { PlatformCapabilities } from '../models/platform-capabilities.model';

@Injectable({ providedIn: 'root' })
export class PlatformIntegrationsService {
  readonly backendChoice = signal<BackendChoiceId>('babo_cloud');
  readonly nestjsUrl = signal('https://api.babo.agency');
  readonly capabilities = signal<PlatformCapabilities | null>(null);
  readonly loading = signal(false);

  constructor(
    private api: ApiService,
    private platform: PlatformService,
  ) {}

  async refresh(): Promise<void> {
    this.loading.set(true);
    try {
      await this.loadBackendChoice();
      try {
        const caps = await firstValueFrom(this.api.getPlatformCapabilities());
        this.capabilities.set(caps);
      } catch {
        this.capabilities.set(null);
      }
    } finally {
      this.loading.set(false);
    }
  }

  private async loadBackendChoice(): Promise<void> {
    if (this.platform.isElectron) {
      try {
        const nls = (window as any).nls;
        const cfg = await nls?.config?.get?.();
        const url = normalizeNestjsUrl(cfg?.nestjsUrl || 'https://api.babo.agency');
        this.nestjsUrl.set(url);
        this.backendChoice.set(matchBackendChoice(url));
        return;
      } catch { /* fall through */ }
    }
    const url = normalizeNestjsUrl(this.api.apiBase.replace(/\/api$/i, ''));
    this.nestjsUrl.set(url);
    this.backendChoice.set(matchBackendChoice(url));
  }
}
