import { TestBed } from '@angular/core/testing';
import { ChatPanelService } from './chat-panel.service';

describe('ChatPanelService', () => {
  let service: ChatPanelService;

  beforeEach(() => {
    localStorage.removeItem('babo_chat_panel_prefs');
    TestBed.configureTestingModule({});
    service = TestBed.inject(ChatPanelService);
  });

  it('toggles left dock tabs exclusively', () => {
    service.toggleLeft('workbench');
    expect(service.leftDock()).toBe('workbench');
    service.toggleLeft('browser');
    expect(service.leftDock()).toBe('browser');
    service.toggleLeft('browser');
    expect(service.leftDock()).toBe('closed');
  });

  it('opens workbench on agentic start', () => {
    service.onAgenticStart();
    expect(service.leftDock()).toBe('workbench');
  });

  it('focus mode closes docks', () => {
    service.openLeft('workbench');
    service.openRight('live');
    service.toggleFocusMode();
    expect(service.focusMode()).toBeTrue();
    expect(service.leftDock()).toBe('closed');
    expect(service.rightDock()).toBe('closed');
  });

  it('increments inbox badge on channel ask_user', () => {
    service.onAskUserFromChannel();
    expect(service.inboxBadge()).toBeGreaterThan(0);
    expect(service.rightDock()).toBe('inbox');
  });
});
