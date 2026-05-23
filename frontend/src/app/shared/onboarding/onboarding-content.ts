export interface OnboardingPageConfig {
  pageKey: string;
  icon: string;
  paragraphCount: number;
  buttonKey?: string;
}

export const ONBOARDING_PAGES: Record<string, OnboardingPageConfig> = {
  dashboard: { pageKey: 'dashboard', icon: '🧬', paragraphCount: 3, buttonKey: 'onboarding_btn.lets_go' },
  create: { pageKey: 'create', icon: '✨', paragraphCount: 2 },
  chat: { pageKey: 'chat', icon: '💬', paragraphCount: 3 },
  brain: { pageKey: 'brain', icon: '🧠', paragraphCount: 2 },
  tools: { pageKey: 'tools', icon: '⚡', paragraphCount: 2 },
};
