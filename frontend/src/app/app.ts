import { Component, signal, OnInit, OnDestroy } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive, Router, NavigationEnd } from '@angular/router';
import { CommonModule } from '@angular/common';
import { Subscription, filter } from 'rxjs';
import { AuthService } from './core/services/auth.service';
import { PlatformService } from './core/services/platform.service';
import { UpdateService } from './core/services/update.service';
import { ToastComponent } from './shared/toast/toast.component';
import { UpdateBannerComponent } from './shared/update-banner/update-banner.component';
import { UpdateModalComponent } from './shared/update-banner/update-modal.component';
import { ThemeService } from './core/services/theme.service';
import { Day1CoachComponent } from './shared/onboarding/day1-coach.component';
import { LocaleService } from './core/locale/locale.service';
import { TranslateModule } from '@ngx-translate/core';
import { environment } from '../environments/environment';
import { openExternalUrl } from './core/services/billing-return.util';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    ToastComponent,
    UpdateBannerComponent,
    UpdateModalComponent,
    Day1CoachComponent,
    TranslateModule,
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit, OnDestroy {
  /** Currently selected agent ID (extracted from routes like /chat/:agentId) */
  activeAgentId = signal<string>('');

  /** Whether we are inside an agent context (show agent-specific tabs) */
  hasAgentContext = signal(false);

  /** Whether we are on the agents dashboard (agent list) */
  isDashboardRoute = signal(false);

  /** Desktop app semver from Electron (e.g. 1.0.0) */
  appVersion = signal<string | null>(null);

  private routeSub!: Subscription;

  constructor(
    public auth: AuthService,
    public platform: PlatformService,
    public updateService: UpdateService,
    public theme: ThemeService,
    private router: Router,
    private locale: LocaleService,
  ) {
    void this.locale.init();
  }

  ngOnInit() {
    void this.loadAppVersion();

    // Track route changes to extract agentId from URL
    this.routeSub = this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => {
        this.syncRouteState(e.urlAfterRedirects);
      });
    this.syncRouteState(this.router.url);
  }

  private syncRouteState(url: string): void {
    const path = url.split('?')[0].split('#')[0];
    const match = path.match(
      /\/(chat|tools|tasks|projects|memory|brain|ide)\/([a-f0-9-]+)/
    );
    if (match) {
      this.activeAgentId.set(match[2]);
      this.hasAgentContext.set(true);
      this.isDashboardRoute.set(false);
    } else {
      this.activeAgentId.set('');
      this.hasAgentContext.set(false);
      this.isDashboardRoute.set(path === '/dashboard' || path === '/');
    }
  }

  ngOnDestroy() {
    this.routeSub?.unsubscribe();
  }

  logout() {
    this.auth.logout();
  }

  openDiscordSupport(): void {
    openExternalUrl(environment.discordSupportUrl);
  }

  private async loadAppVersion(): Promise<void> {
    if (!this.platform.isElectron) return;
    try {
      const nls = (window as { nls?: { getVersion?: () => Promise<string> } }).nls;
      const v = await nls?.getVersion?.();
      if (v) this.appVersion.set(v);
    } catch {
      // Non-critical if preload is unavailable
    }
  }
}
