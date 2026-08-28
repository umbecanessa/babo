import { ChatMessage } from '../services/websocket.service';
import { ChatMainTranscriptService } from '../services/chat-main-transcript.service';
import { ChatWorkbenchService } from '../services/chat-workbench.service';
import { ConversationService } from '../services/conversation.service';

/** Hand off Home promotion without leaking the previous Home tool chips/transcript. */
export function applyHomePromotionHandoff(
  agentId: string,
  outgoingHome: string,
  incomingHome: string,
  deps: {
    conversations: ConversationService;
    workbench: ChatWorkbenchService;
    mainTranscript: ChatMainTranscriptService;
    setMessages: (updater: (msgs: ChatMessage[]) => ChatMessage[]) => void;
  },
): void {
  if (!agentId || !incomingHome || outgoingHome === incomingHome) {
    if (agentId && incomingHome) {
      deps.conversations.setDefaultHomeForAgent(agentId, incomingHome);
    }
    return;
  }

  deps.setMessages((msgs) =>
    deps.conversations.pinLegacyHomeTags(msgs, outgoingHome, agentId),
  );
  deps.workbench.pinLegacyWorkbenchTags(outgoingHome);

  deps.conversations.setDefaultHomeForAgent(agentId, incomingHome);
  deps.workbench.removeEntriesForSession(outgoingHome);
  // Shared Home transcript belongs to the live Home surface — clear for the incoming Home.
  deps.mainTranscript.clear(agentId);
  deps.workbench.setActiveSessionKey(incomingHome);
}
