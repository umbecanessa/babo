import { TestBed } from '@angular/core/testing';
import { WorkspaceTerminalTabsService } from './workspace-terminal-tabs.service';
import { PlatformService } from './platform.service';

describe('WorkspaceTerminalTabsService', () => {
  let svc: WorkspaceTerminalTabsService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        WorkspaceTerminalTabsService,
        {
          provide: PlatformService,
          useValue: { isElectron: true, isRemote: false },
        },
      ],
    });
    svc = TestBed.inject(WorkspaceTerminalTabsService);
  });

  it('creates an agent tab on openPanel in desktop mode', () => {
    svc.openPanel('agent-a');
    expect(svc.tabs('agent-a')().length).toBe(1);
    expect(svc.tabs('agent-a')()[0].kind).toBe('agent');
    expect(svc.panelOpen('agent-a')()).toBe(true);
  });

  it('counts only open panel tabs in openTabCount', () => {
    svc.openPanel('agent-a');
    svc.addStandaloneTab('agent-a');
    expect(svc.tabCount('agent-a')()).toBe(2);
    expect(svc.openTabCount('agent-a')()).toBe(2);
    svc.setPanelOpen('agent-a', false);
    expect(svc.openTabCount('agent-a')()).toBe(0);
    expect(svc.tabCount('agent-a')()).toBe(2);
  });

  it('labels standalone tabs Terminal 1, Terminal 2', () => {
    svc.openPanel('agent-a');
    svc.addStandaloneTab('agent-a');
    svc.addStandaloneTab('agent-a');
    const labels = svc.tabs('agent-a')().filter((t) => t.kind === 'standalone').map((t) => t.label);
    expect(labels).toEqual(['Terminal 1', 'Terminal 2']);
  });

  it('clears per-agent state', () => {
    svc.openPanel('agent-a');
    svc.clearAgent('agent-a');
    expect(svc.tabCount('agent-a')()).toBe(0);
  });
});

describe('WorkspaceTerminalTabsService browser mode', () => {
  it('uses standalone shell when agent mirror unsupported', () => {
    TestBed.configureTestingModule({
      providers: [
        WorkspaceTerminalTabsService,
        {
          provide: PlatformService,
          useValue: { isElectron: false, isRemote: true },
        },
      ],
    });
    const browserSvc = TestBed.inject(WorkspaceTerminalTabsService);
    expect(browserSvc.agentMirrorSupported).toBe(false);
    browserSvc.openPanel('agent-b');
    expect(browserSvc.tabs('agent-b')().every((t) => t.kind === 'standalone')).toBe(true);
  });
});
