import { Component, signal, OnInit, OnDestroy } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive, Router, NavigationEnd } from '@angular/router';
import { CommonModule } from '@angular/common';
import { Subscription, filter } from 'rxjs';
import { TranslateService } from '@ngx-translate/core';
import { AuthService } from './core/services/auth.service';
import { PlatformService } from './core/services/platform.service';
import { UpdateService } from './core/services/update.service';
import { ToastComponent } from './shared/toast/toast.component';
import { UpdateBannerComponent } from './shared/update-banner/update-banner.component';
import { UpdateModalComponent } from './shared/update-banner/update-modal.component';
import { ThemeService } from './core/services/theme.service';
import { Day1CoachComponent } from './shared/onboarding/day1-coach.component';

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
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit, OnDestroy {
  /** Currently selected agent ID (extracted from routes like /chat/:agentId) */
  activeAgentId = signal<string>('');

  /** Whether we are inside an agent context (show agent-specific tabs) */
  hasAgentContext = signal(false);

  /** Desktop app semver from Electron (e.g. 1.0.0) */
  appVersion = signal<string | null>(null);

  private routeSub!: Subscription;

  constructor(
    public auth: AuthService,
    public platform: PlatformService,
    public updateService: UpdateService,
    public theme: ThemeService,
    private router: Router,
    private translate: TranslateService,
  ) {
    this.translate.use('en');
  }

  ngOnInit() {
    void this.loadAppVersion();

    // Track route changes to extract agentId from URL
    this.routeSub = this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => {
        const match = e.urlAfterRedirects.match(
          /\/(chat|tools|tasks|projects|memory|brain|ide)\/([a-f0-9-]+)/
        );
        if (match) {
          this.activeAgentId.set(match[2]);
          this.hasAgentContext.set(true);
        } else {
          this.hasAgentContext.set(false);
        }
      });
  }

  ngOnDestroy() {
    this.routeSub?.unsubscribe();
  }

  logout() {
    this.auth.logout();
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
