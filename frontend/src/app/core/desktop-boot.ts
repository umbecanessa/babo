/** Sync config injected by Electron preload from nls-config.json */

export interface BaboBootConfig {

  nestjsUrl: string;

  apiUrl: string;

  runtimeUrl: string;

  runtimePort: number;

}



type NlsWindow = {

  nls?: {

    isDesktop?: boolean;

    boot?: BaboBootConfig | null;

    getBoot?: () => BaboBootConfig | null;

  };

};



function nlsWindow(): NlsWindow {

  return window as unknown as NlsWindow;

}



/** Latest boot config from main process (re-reads when getBoot is available). */

export function readBaboBoot(): BaboBootConfig | null {

  const nls = nlsWindow().nls;

  if (!nls) {

    return null;

  }

  const fresh = nls.getBoot?.() ?? nls.boot;

  if (!fresh?.apiUrl) {

    return null;

  }

  return fresh;

}



export function isDesktopShell(): boolean {

  return !!nlsWindow().nls?.isDesktop;

}



/** NestJS host root without `/api` suffix. */

export function nestjsRootFromApiBase(apiBase: string): string {

  return apiBase.trim().replace(/\/+$/, '').replace(/\/api$/i, '');

}


