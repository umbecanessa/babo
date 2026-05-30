import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import {
  openExternalUrl,
  resolveCheckoutReturnUrl,
} from './billing-return.util';
import type { CloudSubscriptionView } from '../models/cloud-subscription.model';
import type { PlatformCapabilities } from '../models/platform-capabilities.model';

export interface BillingCheckoutOptions {
  returnUrl?: string;
  flow?: 'setup' | 'settings';
  caps?: PlatformCapabilities | null;
}

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

  /** Reconcile from Stripe when webhooks lag after checkout. */
  async syncFromStripe(): Promise<CloudSubscriptionView | null> {
    try {
      const view = await firstValueFrom(this.api.syncBillingSubscription());
      this.subscription.set(view);
      return view;
    } catch {
      return this.refresh();
    }
  }

  billingEnabledFromCaps(caps: PlatformCapabilities | null): boolean {
    return !!caps?.billing?.enabled;
  }

  async startCheckout(opts: BillingCheckoutOptions = {}): Promise<string | null> {
    this.checkoutLoading.set(true);
    try {
      const flow = opts.flow ?? 'settings';
      const returnUrl =
        opts.returnUrl || resolveCheckoutReturnUrl(flow, opts.caps);
      const res = await firstValueFrom(
        this.api.createBillingCheckout(returnUrl, flow),
      );
      return res.url;
    } finally {
      this.checkoutLoading.set(false);
    }
  }

  async openCheckout(opts: BillingCheckoutOptions = {}): Promise<boolean> {
    const url = await this.startCheckout(opts);
    if (!url) return false;
    openExternalUrl(url);
    return true;
  }

  async openPortal(opts: BillingCheckoutOptions = {}): Promise<string | null> {
    const flow = opts.flow ?? 'settings';
    const returnUrl =
      opts.returnUrl || resolveCheckoutReturnUrl(flow, opts.caps);
    const res = await firstValueFrom(this.api.createBillingPortal(returnUrl, flow));
    return res.url;
  }

  async openPortalExternal(opts: BillingCheckoutOptions = {}): Promise<boolean> {
    const url = await this.openPortal(opts);
    if (!url) return false;
    openExternalUrl(url);
    return true;
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
