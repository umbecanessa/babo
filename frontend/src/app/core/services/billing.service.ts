import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import type { CloudSubscriptionView } from '../models/cloud-subscription.model';
import type { PlatformCapabilities } from '../models/platform-capabilities.model';

@Injectable({ providedIn: 'root' })
export class BillingService {
  private api = inject(ApiService);

  readonly subscription = signal<CloudSubscriptionView | null>(null);
  readonly loading = signal(false);
  readonly checkoutLoading = signal(false);

  readonly billingEnabled = computed(
    () => !!this.subscription()?.billingEnabled,
  );

  readonly usedPercent = computed(
    () => this.subscription()?.usedPercent ?? 0,
  );

  readonly isLifetimeComp = computed(() => {
    const s = this.subscription();
    return !!s && (s.billingExempt || s.status === 'lifetime_comp');
  });

  readonly needsSubscription = computed(() => {
    const s = this.subscription();
    if (!s?.billingEnabled || s.billingExempt) return false;
    return s.status === 'none' || s.status === 'canceled';
  });

  async refresh(): Promise<CloudSubscriptionView | null> {
    this.loading.set(true);
    try {
      const view = await firstValueFrom(this.api.getCloudSubscription());
      this.subscription.set(view);
      return view;
    } catch {
      this.subscription.set(null);
      return null;
    } finally {
      this.loading.set(false);
    }
  }

  billingEnabledFromCaps(caps: PlatformCapabilities | null): boolean {
    return !!caps?.billing?.enabled;
  }

  async startCheckout(returnUrl?: string): Promise<string | null> {
    this.checkoutLoading.set(true);
    try {
      const url =
        returnUrl ||
        `${window.location.origin}/settings?section=billing`;
      const res = await firstValueFrom(this.api.createBillingCheckout(url));
      return res.url;
    } finally {
      this.checkoutLoading.set(false);
    }
  }

  async openPortal(returnUrl?: string): Promise<string | null> {
    const url =
      returnUrl ||
      `${window.location.origin}/settings?section=billing`;
    const res = await firstValueFrom(this.api.createBillingPortal(url));
    return res.url;
  }

  async updateSpendCap(capCents: number | null): Promise<void> {
    await firstValueFrom(this.api.updateBillingSpendCap(capCents));
    await this.refresh();
  }

  async setOnDemandEnabled(enabled: boolean): Promise<void> {
    await firstValueFrom(this.api.setBillingOnDemand(enabled));
    await this.refresh();
  }
}
