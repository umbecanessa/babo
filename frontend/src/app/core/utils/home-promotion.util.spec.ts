import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { ConversationService } from '../services/conversation.service';
import { ChatMainTranscriptService } from '../services/chat-main-transcript.service';
import { ChatWorkbenchService } from '../services/chat-workbench.service';
import { applyHomePromotionHandoff } from './home-promotion.util';

describe('applyHomePromotionHandoff', () => {
  let conversations: ConversationService;
  let mainTranscript: ChatMainTranscriptService;
  let workbench: ChatWorkbenchService;
  const messages = signal<any[]>([]);

  beforeEach(() => {
    TestBed.configureTestingModule({});
    conversations = TestBed.inject(ConversationService);
    mainTranscript = TestBed.inject(ChatMainTranscriptService);
    workbench = TestBed.inject(ChatWorkbenchService);
    conversations.setDefaultHomeForAgent('agent-a', 'websocket:thread:old-home');
    messages.set([
      { sessionKey: 'websocket:main', type: 'tool_progress', content: 'bash done' },
      { sessionKey: 'websocket:thread:old-home', type: 'assistant', content: 'hello' },
    ]);
    mainTranscript.replace('agent-a', [
      { sessionKey: 'websocket:main', type: 'tool_progress', content: 'shared chip' } as any,
    ]);
    workbench.bindAgent('agent-a');
    workbench.restoreState(false, [{
      id: '1',
      ts: 1,
      lane: 'chat',
      kind: 'tool',
      title: 'Read file',
      status: 'done',
      sessionKey: 'websocket:main',
    } as any]);
  });

  it('pins legacy home tags and clears shared transcript for the new home', () => {
    applyHomePromotionHandoff(
      'agent-a',
      'websocket:thread:old-home',
      'websocket:thread:new-home',
      {
        conversations,
        workbench,
        mainTranscript,
        setMessages: (updater) => messages.set(updater(messages())),
      },
    );

    expect(conversations.defaultHomeKey('agent-a')).toBe('websocket:thread:new-home');
    expect(messages().find((m) => m.content === 'bash done')?.sessionKey)
      .toBe('websocket:thread:old-home');
    expect(conversations.messagesForThread(messages(), 'websocket:thread:new-home', 'agent-a').length)
      .toBe(0);
    expect(mainTranscript.get('agent-a').length).toBe(0);
    expect(workbench.snapshotState().entries.length).toBe(0);
  });
});
