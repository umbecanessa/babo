import { TestBed } from '@angular/core/testing';
import { RunViewService } from './run-view.service';

describe('RunViewService', () => {
  let svc: RunViewService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    svc = TestBed.inject(RunViewService);
    svc.bindAgent('agent-test');
  });

  it('hydrates plan steps from agentic_plan', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build app',
      steps: [
        { id: 'step-1', label: 'Scaffold', status: 'pending', delegatable: true },
        { id: 'step-2', label: 'API', status: 'pending', delegatable: true },
      ],
    });
    expect(svc.visible()).toBe(true);
    expect(svc.totalCount()).toBe(2);
    expect(svc.title()).toBe('Build app');
  });

  it('nests delegate under plan step by step_id', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build',
      steps: [{ id: 'step-2', label: 'Database', status: 'in_progress', delegatable: true }],
    });
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 1,
      delegate_task: 'Create schema',
      step_id: 'step-2',
      team_id: 'team_x',
    });
    const step = svc.steps().find(s => s.id === 'step-2');
    expect(step?.delegates.length).toBe(1);
    expect(step?.delegates[0].number).toBe(1);
  });

  it('updates plan step from plan_step_update', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      steps: [{ id: 'step-1', label: 'One', status: 'pending' }],
    });
    svc.handleMessage({ type: 'plan_step_update', step_index: 0, status: 'done' });
    expect(svc.doneCount()).toBe(1);
  });

  it('marks plan step done when all delegates on that step finish', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      steps: [
        {
          id: 'step-db',
          label: 'Database Schema & Migration Files',
          status: 'active',
        },
      ],
    });
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 0,
      delegate_task: 'Schema work',
      step_id: 'step-db',
    });
    svc.handleMessage({
      type: 'delegate_end',
      delegate_number: 0,
      iterations: 5,
      tool_calls: 12,
      summary: 'Migrations written',
    });
    const step = svc.steps().find(s => s.id === 'step-db');
    expect(step?.status).toBe('done');
    expect(step?.delegates[0]?.status).toBe('done');
  });

  it('records delegate tool calls', () => {
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 0,
      delegate_task: 'Work',
      step_id: 'step-1',
    });
    svc.handleMessage({
      type: 'agentic_plan',
      steps: [{ id: 'step-1', label: 'Work', status: 'active' }],
    });
    svc.handleMessage({
      type: 'tool_execution_start',
      sub_agent: true,
      delegate_number: 0,
      tool_name: 'write',
      call_id: 'c1',
      arguments: { path: 'main.py' },
    });
    svc.handleMessage({
      type: 'tool_execution_end',
      sub_agent: true,
      delegate_number: 0,
      tool_name: 'write',
      call_id: 'c1',
      is_error: false,
    });
    const d = svc.steps()[0]?.delegates[0] ?? svc.unassignedDelegates()[0];
    expect(d?.toolCalls.length).toBe(1);
    expect(d?.toolCalls[0].result).toBe('done');
  });

  it('snapshots and restores state', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'p1',
      title: 'T',
      steps: [{ id: 's1', label: 'L', status: 'done' }],
    });
    const saved = svc.persisted();
    svc.clear();
    expect(svc.visible()).toBe(false);
    svc.restorePersisted(saved);
    expect(svc.visible()).toBe(true);
    expect(svc.doneCount()).toBe(1);
  });

  it('hydrates queued wave members before delegate spawn', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build',
      steps: [
        { id: 'step-1', label: 'Scaffold', status: 'pending', delegatable: true },
        { id: 'step-2', label: 'Database', status: 'pending', delegatable: true },
        { id: 'step-3', label: 'Frontend', status: 'pending', delegatable: true },
      ],
    });
    svc.hydrateTeams([{
      id: 'team_1',
      name: 'Wave 0',
      plan_id: 'plan_a',
      wave_index: 0,
      wave_attempt: 1,
      status: 'active',
      mission: '',
      briefing: '',
      members: [
        { delegate_number: 0, step_id: 'step-1', task: 'Scaffold', status: 'running', result_summary: '', kanban_task_id: '', iterations: 1, tool_calls: 2, elapsed_seconds: 0 },
        { delegate_number: -1, step_id: 'step-2', task: 'Database schema', status: 'pending', result_summary: '', kanban_task_id: '', iterations: 0, tool_calls: 0, elapsed_seconds: 0 },
        { delegate_number: -1, step_id: 'step-3', task: 'Frontend shell', status: 'pending', result_summary: '', kanban_task_id: '', iterations: 0, tool_calls: 0, elapsed_seconds: 0 },
      ],
      batch_id: '',
      checkback_job: '',
      kanban_parent_id: '',
      results_log: [],
      created_at: 1,
      completed_at: 0,
    }]);
    expect(svc.steps().find(s => s.id === 'step-2')?.delegates.length).toBe(1);
    expect(svc.steps().find(s => s.id === 'step-2')?.delegates[0].status).toBe('queued');
    expect(svc.runningDelegateCount()).toBe(3);
    expect(svc.isLive()).toBe(true);
    expect(svc.collapsedActivity().length).toBe(3);
    expect(svc.collapsedActivity().filter(a => a.status === 'queued').length).toBe(2);
  });

  it('resolveMemberTarget maps wave index to delegate_number', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build',
      steps: [
        { id: 'step-2', label: 'Database Schema', status: 'pending', delegatable: true },
        { id: 'step-7', label: 'Frontend Shell', status: 'pending', delegatable: true },
        { id: 'step-3', label: 'Backend API Foundation', status: 'pending', delegatable: true },
      ],
    });
    svc.hydrateTeams([{
      id: 'team_w2',
      name: 'Wave 2',
      plan_id: 'plan_a',
      wave_index: 1,
      wave_attempt: 1,
      status: 'active',
      mission: '',
      briefing: '',
      members: [
        { delegate_number: 1, step_id: 'step-2', task: 'Database Schema\n\nDescription', status: 'running', result_summary: '', kanban_task_id: '', iterations: 0, tool_calls: 0, elapsed_seconds: 0 },
        { delegate_number: 2, step_id: 'step-7', task: 'Frontend Shell', status: 'running', result_summary: '', kanban_task_id: '', iterations: 0, tool_calls: 0, elapsed_seconds: 0 },
        { delegate_number: 3, step_id: 'step-3', task: 'Backend API Foundation', status: 'running', result_summary: '', kanban_task_id: '', iterations: 0, tool_calls: 0, elapsed_seconds: 0 },
      ],
      batch_id: 'batch_1',
      checkback_job: '',
      kanban_parent_id: '',
      results_log: [],
      created_at: 1,
      completed_at: 0,
    }]);
    const target = svc.resolveMemberTarget('team_w2', 2);
    expect(target?.delegateNumber).toBe(3);
    expect(target?.stepLabel).toBe('Backend API Foundation');
  });

  it('keeps one delegate when delegate_start and team hydrate overlap', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build',
      steps: [{ id: 'step-1', label: 'Scaffold', status: 'active', delegatable: true }],
    });
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 0,
      delegate_task: 'Scaffold repo',
      step_id: 'step-1',
      team_id: 'team_1',
      member_idx: 0,
    });
    svc.hydrateTeams([{
      id: 'team_1',
      name: 'Wave 0',
      plan_id: 'plan_a',
      wave_index: 0,
      wave_attempt: 1,
      status: 'active',
      mission: '',
      briefing: '',
      members: [{
        delegate_number: 0,
        step_id: 'step-1',
        task: 'Scaffold repo',
        status: 'running',
        result_summary: '',
        kanban_task_id: '',
        iterations: 1,
        tool_calls: 2,
        elapsed_seconds: 0,
      }],
      batch_id: '',
      checkback_job: '',
      kanban_parent_id: '',
      results_log: [],
      created_at: 1,
      completed_at: 0,
    }]);
    const step = svc.steps().find(s => s.id === 'step-1');
    expect(step?.delegates.length).toBe(1);
    expect(step?.delegates[0].number).toBe(0);
    expect(svc.runningDelegateCount()).toBe(1);
  });

  it('merges delegate_start without team key and later hydrate', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      steps: [{ id: 'step-1', label: 'Scaffold', status: 'active', delegatable: true }],
    });
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 0,
      delegate_task: 'Scaffold repo',
      step_id: 'step-1',
    });
    svc.hydrateTeams([{
      id: 'team_1',
      name: 'Wave 0',
      plan_id: 'plan_a',
      wave_index: 0,
      wave_attempt: 1,
      status: 'active',
      mission: '',
      briefing: '',
      members: [{
        delegate_number: 0,
        step_id: 'step-1',
        task: 'Scaffold repo',
        status: 'running',
        result_summary: '',
        kanban_task_id: '',
        iterations: 0,
        tool_calls: 0,
        elapsed_seconds: 0,
      }],
      batch_id: '',
      checkback_job: '',
      kanban_parent_id: '',
      results_log: [],
      created_at: 1,
      completed_at: 0,
    }]);
    expect(svc.steps().find(s => s.id === 'step-1')?.delegates.length).toBe(1);
  });

  it('does not show plan in_progress as active without a live delegate', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build',
      steps: [
        { id: 'step-1', label: 'Scaffold', status: 'in_progress', delegatable: true },
        { id: 'step-4', label: 'AssemblyAI', status: 'in_progress', delegatable: true },
        { id: 'step-5', label: 'Anthropic', status: 'in_progress', delegatable: true },
      ],
    });
    svc.hydrateTeams([{
      id: 'team_1',
      name: 'Wave 0',
      plan_id: 'plan_a',
      wave_index: 0,
      wave_attempt: 1,
      status: 'active',
      mission: '',
      briefing: '',
      members: [{
        delegate_number: 0,
        step_id: 'step-1',
        task: 'Scaffold',
        status: 'running',
        result_summary: '',
        kanban_task_id: '',
        iterations: 0,
        tool_calls: 0,
        elapsed_seconds: 0,
      }],
      batch_id: '',
      checkback_job: '',
      kanban_parent_id: '',
      results_log: [],
      created_at: 1,
      completed_at: 0,
    }]);
    expect(svc.steps().find(s => s.id === 'step-1')?.status).toBe('active');
    expect(svc.steps().find(s => s.id === 'step-4')?.status).toBe('pending');
    expect(svc.steps().find(s => s.id === 'step-5')?.status).toBe('pending');
    expect(svc.runningDelegateCount()).toBe(1);
    expect(svc.isLive()).toBe(true);
  });

  it('accept_partial marks step done and clears failed delegate overlay', () => {
    svc.handleMessage({
      type: 'agentic_plan',
      plan_id: 'plan_a',
      title: 'Build',
      steps: [{ id: 'step-5', label: 'Integrate', status: 'pending' }],
    });
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 4,
      delegate_task: 'Integrate API',
      step_id: 'step-5',
    });
    svc.handleMessage({
      type: 'delegate_end',
      delegate_number: 4,
      aborted: true,
      summary: 'PATH NOT IN YOUR ASSIGNMENT',
    });
    expect(svc.steps().find(s => s.id === 'step-5')?.status).toBe('error');
    svc.handleMessage({
      type: 'tool_execution_end',
      tool_name: 'plan',
      is_error: false,
      details: { action: 'accept_partial', step_id: 'step-5' },
    });
    const step = svc.steps().find(s => s.id === 'step-5');
    expect(step?.status).toBe('done');
    expect(step?.partialAccept).toBe(true);
    expect(step?.delegates[0]?.status).toBe('done');
    expect(svc.recoveryPending()).toBe(true);
  });

  it('tracks detached delegate batch without plan steps', () => {
    svc.handleMessage({
      type: 'delegate_batch_started',
      batch_id: 'eb4390cd0eb5',
      count: 5,
      simple_delegate: true,
    });
    expect(svc.visible()).toBe(false);
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 1,
      delegate_task: 'Study game-api',
      batch_id: 'eb4390cd0eb5',
    });
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 2,
      delegate_task: 'Study platform-api',
      batch_id: 'eb4390cd0eb5',
    });
    expect(svc.visible()).toBe(true);
    expect(svc.batchId()).toBe('eb4390cd0eb5');
    expect(svc.batchCount()).toBe(5);
    expect(svc.displayTitle()).toContain('Sub-agents');
    expect(svc.unassignedDelegates().length).toBe(2);
    expect(svc.runningDelegateCount()).toBe(2);
    expect(svc.liveDelegates().length).toBe(2);
    expect(svc.expanded()).toBe(true);
  });

  it('hydrateDelegates seeds batch and running delegates from API', () => {
    svc.hydrateDelegates({
      batches: {
        eb4390: { batch_id: 'eb4390', total: 2, completed: 1 },
      },
      delegates: [
        {
          delegate_number: 1,
          task: 'Study api',
          batch_id: 'eb4390',
          state: 'running',
          iteration: 3,
          max_iterations: 25,
        },
        {
          delegate_number: 2,
          task: 'Study ui',
          batch_id: 'eb4390',
          state: 'done',
          timed_out: true,
          partial: true,
          summary: '(sub-agent timed out)\n\n[DELEGATE KNOWLEDGE DIGEST]',
        },
      ],
    });
    expect(svc.batchId()).toBe('eb4390');
    expect(svc.runningDelegateCount()).toBe(1);
    const d2 = svc.unassignedDelegates().find(d => d.number === 2);
    expect(d2?.status).toBe('done');
    expect(d2?.partialTimeout).toBe(true);
  });

  it('delegate_end partial timeout marks done not error', () => {
    svc.handleMessage({
      type: 'delegate_start',
      delegate_number: 3,
      delegate_task: 'Study admin',
    });
    svc.handleMessage({
      type: 'delegate_end',
      delegate_number: 3,
      aborted: false,
      partial: true,
      summary: '(sub-agent timed out after 900s)\n\n[DELEGATE KNOWLEDGE DIGEST]',
    });
    const d = svc.unassignedDelegates().find(x => x.number === 3);
    expect(d?.status).toBe('done');
    expect(d?.partialTimeout).toBe(true);
  });
});
