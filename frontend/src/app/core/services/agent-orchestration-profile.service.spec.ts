import { AgentOrchestrationProfileService } from './agent-orchestration-profile.service';

describe('AgentOrchestrationProfileService', () => {
  let svc: AgentOrchestrationProfileService;

  beforeEach(() => {
    svc = new AgentOrchestrationProfileService();
    svc.setActiveAgent('agent-1');
  });

  it('shows depth only when idle', () => {
    svc.setChoice('agent-1', 'solo_structured');
    expect(svc.depthLabel('agent-1')).toBe('Solo');
    expect(svc.modeLabel('agent-1')).toBeNull();
    expect(svc.triggerLabel('agent-1')).toBe('Solo');
  });

  it('combines depth and runtime mode during agentic runs', () => {
    svc.setChoice('agent-1', 'solo_structured');
    svc.setAgenticActive('agent-1', true);
    svc.setRuntimeMode('agent-1', 'planning');
    expect(svc.triggerLabel('agent-1')).toBe('Solo · Planning');
  });

  it('shows triage resolution under auto', () => {
    svc.noteTriageProfile('agent-1', { profile: 'orchestrated' });
    expect(svc.depthLabel('agent-1')).toBe('Auto · EM');
  });
});
