/**
 * NLS IDE -- Web-based Integrated Development Environment
 *
 * Layout:
 *   - Left:   File Explorer (tree view, lazy-loaded)
 *   - Center: Monaco Editor (multi-tab, syntax highlighting)
 *   - Bottom: xterm.js Terminal (persistent shell via WebSocket)
 *   - Right:  AI Chat Sidebar (agent-aware, streams tool events)
 *
 * Works in any browser -- no Electron required.
 * Uses the NLS backend /fs/* endpoints for file operations
 * and /terminal WebSocket for the integrated terminal.
 */

import {
  Component,
  OnInit,
  OnDestroy,
  AfterViewInit,
  signal,
  computed,
  ViewChild,
  ElementRef,
  NgZone,
  HostListener,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Subscription } from 'rxjs';

import { FilesystemService, FileEntry } from '../../core/services/filesystem.service';
import { TerminalService } from '../../core/services/terminal.service';
import { WebSocketService } from '../../core/services/websocket.service';
import { ApiService } from '../../core/services/api.service';
import { ToastService } from '../../shared/toast/toast.service';
import { PlatformService } from '../../core/services/platform.service';
import {
  parseTags,
  stripTags,
  tagColor,
  humanType,
  SignalTag,
} from '../../shared/signal-utils';

// Monaco loaded via ESM dynamic import (avoids AMD loader conflicts with Electron)
let _monacoModule: any = null;
let _monacoLoadPromise: Promise<any> | null = null;

/**
 * Worker file map for Monaco 0.55.x
 * These are the pre-built self-contained worker bundles shipped in
 * node_modules/monaco-editor/min/vs/assets/ and copied via angular.json
 * to /monaco-workers/ in the build output.
 */
const WORKER_MAP: Record<string, string> = {
  json: 'json.worker-DKiEKt88.js',
  css: 'css.worker-HnVq6Ewq.js',
  scss: 'css.worker-HnVq6Ewq.js',
  less: 'css.worker-HnVq6Ewq.js',
  html: 'html.worker-B51mlPHg.js',
  handlebars: 'html.worker-B51mlPHg.js',
  razor: 'html.worker-B51mlPHg.js',
  typescript: 'ts.worker-CMbG-7ft.js',
  javascript: 'ts.worker-CMbG-7ft.js',
  _default: 'editor.worker-Be8ye1pW.js',
};

/** Singleton loader -- ensures Monaco is loaded exactly once. */
function loadMonacoESM(): Promise<any> {
  if (_monacoModule) return Promise.resolve(_monacoModule);
  if (_monacoLoadPromise) return _monacoLoadPromise;

  // Configure worker resolution BEFORE importing Monaco
  (self as any).MonacoEnvironment = {
    getWorkerUrl(_workerId: string, label: string) {
      const file = WORKER_MAP[label] || WORKER_MAP['_default'];
      return `/monaco-workers/${file}`;
    },
  };

  _monacoLoadPromise = import('monaco-editor').then((mod) => {
    _monacoModule = mod;
    return mod;
  });

  return _monacoLoadPromise;
}

interface FileNode {
  name: string;
  path: string;
  isDirectory: boolean;
  size: number;
  children?: FileNode[];
  expanded?: boolean;
  loading?: boolean;
}

interface OpenTab {
  path: string;
  name: string;
  content: string;
  modified: boolean;
  language: string;
  model?: any; // monaco.editor.ITextModel
}

@Component({
  selector: 'app-ide',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './ide.component.html',
  styleUrl: './ide.component.scss',
})
export class IdeComponent implements OnInit, OnDestroy, AfterViewInit {
  @ViewChild('editorContainer') editorContainer!: ElementRef<HTMLDivElement>;
  @ViewChild('terminalContainer') terminalContainer!: ElementRef<HTMLDivElement>;
  @ViewChild('chatInput') chatInputEl!: ElementRef<HTMLTextAreaElement>;

  // ── State ────────────────────────────────────────────────────
  projectRoot = signal('');
  projectName = signal('');
  fileTree = signal<FileNode[]>([]);
  openTabs = signal<OpenTab[]>([]);
  activeTabIndex = signal(-1);
  showFileTree = signal(true);
  showTerminal = signal(true);
  showChat = signal(true);
  terminalHeight = signal(220);
  chatWidth = signal(340);
  folderInput = '';
  agentId = '';

  // Chat state
  chatMessages = signal<any[]>([]);
  chatInput = '';
  streamingText = signal('');
  nlsMetadata = signal<any>(null);
  expandedDriveActions = new Set<number>();

  activeTab = computed(() => {
    const tabs = this.openTabs();
    const idx = this.activeTabIndex();
    return idx >= 0 && idx < tabs.length ? tabs[idx] : null;
  });

  private editor: any = null; // monaco.editor.IStandaloneCodeEditor
  private terminal: any = null; // Terminal instance
  private fitAddon: any = null;
  private subs: Subscription[] = [];
  private monacoLoaded = false;

  constructor(
    private route: ActivatedRoute,
    private fs: FilesystemService,
    private terminalService: TerminalService,
    private ws: WebSocketService,
    private api: ApiService,
    private toast: ToastService,
    private zone: NgZone,
    public platform: PlatformService,
  ) {}

  // ── Lifecycle ──────────────────────────────────────────────────

  ngOnInit(): void {
    this.agentId = this.route.snapshot.params['agentId'];

    // Connect chat WebSocket
    this.ws.connect();
    this.ws.joinAgent(this.agentId);
    this.subs.push(
      // Plain stream — legacy IDE; do not use onMessage(agentId) (reserves chat replay slot).
      this.ws.onMessage().subscribe((msg) => this.handleChatMessage(msg)),
    );

    // Connect terminal WebSocket
    this.terminalService.connect();
    this.subs.push(
      this.terminalService.onOutput().subscribe((out) => {
        if (out.type === 'output' && out.data && this.terminal) {
          this.terminal.write(out.data);
        }
      }),
    );
  }

  ngAfterViewInit(): void {
    this.loadMonaco();
    this.initTerminal();
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    // Leave chat WebSocket open (shared with Chat/Tasks for same agent).
    this.terminalService.disconnect();
    this.editor?.dispose();
    this.terminal?.dispose();
    this.terminalResizeObserver?.disconnect();
  }

  @HostListener('window:keydown', ['$event'])
  handleKeydown(e: KeyboardEvent): void {
    if (e.ctrlKey || e.metaKey) {
      if (e.key === 's') {
        e.preventDefault();
        this.saveCurrentFile();
      } else if (e.key === '`') {
        e.preventDefault();
        this.showTerminal.update((v) => !v);
        setTimeout(() => this.fitAddon?.fit(), 50);
      } else if (e.key === 'b') {
        e.preventDefault();
        this.showChat.update((v) => !v);
        setTimeout(() => this.editor?.layout(), 50);
      }
    }
  }

  // ── Monaco Editor ──────────────────────────────────────────────

  private monacoApi: any = null; // Reference to the monaco-editor ESM module

  private loadMonaco(): void {
    loadMonacoESM().then((mod) => {
      this.zone.run(() => {
        this.monacoApi = mod;
        this.monacoLoaded = true;
        this.createEditor();
      });
    }).catch((err) => {
      console.error('Failed to load Monaco Editor:', err);
    });
  }

  private createEditor(): void {
    if (!this.editorContainer?.nativeElement || !this.monacoApi) return;

    this.editor = this.monacoApi.editor.create(this.editorContainer.nativeElement, {
      value: '',
      language: 'plaintext',
      theme: 'vs-dark',
      fontSize: 13,
      fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
      minimap: { enabled: true },
      automaticLayout: true,
      wordWrap: 'off',
      scrollBeyondLastLine: false,
      renderLineHighlight: 'gutter',
      cursorBlinking: 'smooth',
      smoothScrolling: true,
      padding: { top: 8, bottom: 8 },
      suggest: { showMethods: true, showFunctions: true },
    });

    // Track modifications
    this.editor.onDidChangeModelContent(() => {
      const tab = this.activeTab();
      if (tab && this.editor) {
        const newContent = this.editor.getValue();
        if (newContent !== tab.content) {
          this.zone.run(() => {
            this.openTabs.update((tabs) => {
              const newTabs = [...tabs];
              const idx = this.activeTabIndex();
              if (idx >= 0 && idx < newTabs.length) {
                newTabs[idx] = { ...newTabs[idx], content: newContent, modified: true };
              }
              return newTabs;
            });
          });
        }
      }
    });
  }

  private setEditorContent(content: string, language: string): void {
    if (!this.editor || !this.monacoLoaded || !this.monacoApi) return;

    const monacoLang = this.mapLanguageId(language);
    const model = this.editor.getModel();
    if (model) {
      this.monacoApi.editor.setModelLanguage(model, monacoLang);
      model.setValue(content);
    }
  }

  // ── xterm.js Terminal ──────────────────────────────────────────

  private terminalResizeObserver: ResizeObserver | null = null;

  private async initTerminal(): Promise<void> {
    const { Terminal } = await import('@xterm/xterm');
    const { FitAddon } = await import('@xterm/addon-fit');
    const { WebLinksAddon } = await import('@xterm/addon-web-links');

    this.terminal = new Terminal({
      fontSize: 13,
      fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
      theme: {
        background: '#0d0d0d',
        foreground: '#e0e0e0',
        cursor: '#2563eb',
        selectionBackground: '#2563eb40',
      },
      cursorBlink: true,
      allowProposedApi: true,
      convertEol: false,
    });

    this.fitAddon = new FitAddon();
    this.terminal.loadAddon(this.fitAddon);
    this.terminal.loadAddon(new WebLinksAddon());

    if (this.terminalContainer?.nativeElement) {
      this.terminal.open(this.terminalContainer.nativeElement);

      // Initial fit + send dimensions to backend PTY
      setTimeout(() => {
        this.fitAddon.fit();
        const dims = this.fitAddon.proposeDimensions();
        if (dims) {
          this.terminalService.resize(dims.cols, dims.rows);
        }
      }, 50);

      // Watch for container size changes and re-fit
      this.terminalResizeObserver = new ResizeObserver(() => {
        try {
          this.fitAddon?.fit();
        } catch {
          // ignore if terminal disposed
        }
      });
      this.terminalResizeObserver.observe(this.terminalContainer.nativeElement);
    }

    // Forward user input to backend
    this.terminal.onData((data: string) => {
      this.terminalService.sendInput(data);
    });

    // When xterm resizes, sync the PTY dimensions
    this.terminal.onResize((size: { cols: number; rows: number }) => {
      this.terminalService.resize(size.cols, size.rows);
    });
  }

  // ── File Explorer ──────────────────────────────────────────────

  async openFolder(): Promise<void> {
    let folderPath = this.folderInput.trim();

    // In Electron, open a native folder picker if no path typed
    if (!folderPath && this.platform.isElectron) {
      const nls = (window as any).nls;
      if (nls?.showOpenDialog) {
        const result = await nls.showOpenDialog({
          properties: ['openDirectory'],
          title: 'Open Folder',
        });
        if (result.canceled || !result.filePaths?.length) return;
        folderPath = result.filePaths[0];
        this.folderInput = folderPath;
      }
    }

    if (!folderPath) return;

    this.projectRoot.set(folderPath);
    this.projectName.set(folderPath.split(/[/\\]/).pop() || folderPath);
    await this.refreshTree();

    // Set terminal cwd
    this.terminalService.setCwd(folderPath);
  }

  async refreshTree(): Promise<void> {
    const root = this.projectRoot();
    if (!root) return;

    this.fs.readDir(root).subscribe({
      next: (res) => {
        this.fileTree.set(
          res.entries.map((e) => ({
            ...e,
            expanded: false,
          })),
        );
      },
      error: (err) => {
        this.toast.show('Failed to read directory: ' + (err.error?.message || err.message), 'error');
      },
    });
  }

  async onTreeItemClick(node: FileNode): Promise<void> {
    if (node.isDirectory) {
      node.expanded = !node.expanded;
      if (node.expanded && !node.children) {
        node.loading = true;
        this.fs.readDir(node.path).subscribe({
          next: (res) => {
            node.children = res.entries.map((e) => ({
              ...e,
              expanded: false,
            }));
            node.loading = false;
            this.fileTree.update((t) => [...t]); // trigger re-render
          },
          error: () => {
            node.loading = false;
          },
        });
      }
      this.fileTree.update((t) => [...t]);
    } else {
      this.openFile(node.path);
    }
  }

  // ── Editor Tabs ────────────────────────────────────────────────

  openFile(filePath: string): void {
    // If already open, switch to it
    const existing = this.openTabs().findIndex((t) => t.path === filePath);
    if (existing >= 0) {
      this.switchTab(existing);
      return;
    }

    // Read from backend
    this.fs.readFile(filePath).subscribe({
      next: (res) => {
        const name = filePath.split(/[/\\]/).pop() || '';
        const language = this.detectLanguage(name);
        const tab: OpenTab = {
          path: filePath,
          name,
          content: res.content,
          modified: false,
          language,
        };
        this.openTabs.update((tabs) => [...tabs, tab]);
        this.activeTabIndex.set(this.openTabs().length - 1);
        this.setEditorContent(res.content, language);
      },
      error: (err) => {
        this.toast.show('Failed to read file: ' + (err.error?.message || err.message), 'error');
      },
    });
  }

  switchTab(index: number): void {
    this.activeTabIndex.set(index);
    const tab = this.openTabs()[index];
    if (tab) {
      this.setEditorContent(tab.content, tab.language);
    }
  }

  closeTab(index: number, event?: Event): void {
    event?.stopPropagation();
    this.openTabs.update((tabs) => {
      const newTabs = [...tabs];
      newTabs.splice(index, 1);
      return newTabs;
    });

    const tabs = this.openTabs();
    if (this.activeTabIndex() >= tabs.length) {
      this.activeTabIndex.set(tabs.length - 1);
    }

    const newActive = this.activeTab();
    if (newActive) {
      this.setEditorContent(newActive.content, newActive.language);
    } else if (this.editor) {
      this.editor.setValue('');
    }
  }

  saveCurrentFile(): void {
    const tab = this.activeTab();
    if (!tab || !tab.modified) return;

    const content = this.editor ? this.editor.getValue() : tab.content;

    this.fs.writeFile(tab.path, content).subscribe({
      next: () => {
        this.openTabs.update((tabs) => {
          const newTabs = [...tabs];
          const idx = this.activeTabIndex();
          if (idx >= 0) {
            newTabs[idx] = { ...newTabs[idx], content, modified: false };
          }
          return newTabs;
        });
        this.toast.show(`Saved ${tab.name}`, 'info', 2000);
      },
      error: (err) => {
        this.toast.show('Failed to save: ' + (err.error?.message || err.message), 'error');
      },
    });
  }

  // ── Chat Sidebar ───────────────────────────────────────────────

  sendChat(): void {
    const text = this.chatInput.trim();
    if (!text) return;

    this.chatMessages.update((msgs) => [
      ...msgs,
      { type: 'user', content: text, timestamp: new Date() },
    ]);
    this.ws.sendMessage(text);
    this.chatInput = '';
    this.streamingText.set('');
  }

  onChatKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendChat();
    }
  }

  private handleChatMessage(msg: any): void {
    switch (msg.type) {
      case 'history':
        if (Array.isArray(msg.messages) && msg.messages.length > 0) {
          const restored = msg.messages.map((m: any) => ({
            type: m.role === 'user' ? 'user' : 'assistant',
            content: m.content || '',
            timestamp: new Date(),
          }));
          this.chatMessages.set(restored);
        }
        break;

      case 'token':
        this.streamingText.update((t) => t + msg.content);
        break;

      case 'response_end': {
        // Prefer msg.response (server-side cleaned text) over streamingText()
        // which may contain orphan </think> artifacts from the raw token stream.
        const fullText = msg.response || this.streamingText();
        this.chatMessages.update((msgs) => [
          ...msgs,
          {
            type: 'assistant',
            content: fullText,
            timestamp: new Date(),
            nls: msg.nls,
          },
        ]);
        this.streamingText.set('');
        if (msg.nls) {
          this.nlsMetadata.update((m) => ({ ...m, ...msg.nls }));
        }
        break;
      }

      case 'status': {
        const statusText = msg.agent_status || msg.content || '';
        if (statusText === 'sleeping') {
          this.chatMessages.update((msgs) => [
            ...msgs,
            {
              type: 'status',
              content: `Agent is sleeping...`,
              timestamp: new Date(),
            },
          ]);
        }
        if (msg.hormones) {
          this.nlsMetadata.update((m) => ({
            ...m,
            hormones: msg.hormones,
            facts_in_memory: msg.facts_in_memory ?? m?.facts_in_memory ?? 0,
          }));
        }
        break;
      }

      case 'tool_use':
        this.chatMessages.update((msgs) => [
          ...msgs,
          {
            type: 'tool_use',
            content: msg.query || '',
            timestamp: new Date(),
            tool: {
              name: msg.tool || 'web_search',
              query: msg.query || '',
              source: msg.source || '',
              preview: msg.result_preview || '',
              success: msg.success !== false,
            },
          },
        ]);
        // If the agent used file tools, refresh the affected tab
        if (msg.tool === 'file_write' || msg.tool === 'file_edit') {
          this.refreshOpenTabByPath(msg.path);
        }
        break;

      case 'drive_action': {
        if (msg.action_type === 'reach_out') {
          // Social reach-out: show as a chat bubble with green accent
          this.chatMessages.update((msgs) => [
            ...msgs,
            {
              type: 'reach_out',
              content: msg.result_preview || msg.query || '',
              timestamp: new Date(),
              drive: {
                name: msg.drive || '',
                actionType: msg.action_type,
                domain: msg.domain || '',
                query: msg.query || '',
                success: msg.success !== false,
                resultPreview: msg.result_preview || '',
                willToAct: msg.will_to_act || 0,
              },
            },
          ]);
        } else {
          // Web search / Wikipedia / browse: show as expandable card
          this.chatMessages.update((msgs) => [
            ...msgs,
            {
              type: 'drive_action',
              content: msg.query || '',
              timestamp: new Date(),
              drive: {
                name: msg.drive || '',
                actionType: msg.action_type || '',
                domain: msg.domain || '',
                query: msg.query || '',
                success: msg.success !== false,
                resultPreview: msg.result_preview || '',
                willToAct: msg.will_to_act || 0,
              },
            },
          ]);
        }
        break;
      }

      case 'reach_out': {
        // Proactive initiative: show as green-bordered chat bubble
        this.chatMessages.update((msgs) => [
          ...msgs,
          {
            type: 'reach_out',
            content: msg.content || '',
            timestamp: new Date(),
          },
        ]);
        break;
      }

      case 'daydream': {
        const parsed = parseTags(msg.content || 'Agent daydreaming...');
        this.toast.show(parsed.text, 'dream', 10000, parsed.tags);
        break;
      }

      case 'drowsy': {
        // Agent is drowsy -- show as a chat bubble with action buttons
        this.chatMessages.update((msgs) => [
          ...msgs,
          {
            type: 'drowsy' as any,
            content: msg.content || "I'm feeling drowsy...",
            timestamp: new Date(),
            drowsy: {
              reason: msg.reason || '',
              actions: msg.actions || ['yes', 'no'],
            },
          },
        ]);
        break;
      }
    }
  }

  drowsyResponded = new Set<number>();

  onDrowsyAction(index: number, action: 'confirm' | 'deny'): void {
    this.drowsyResponded.add(index);
    if (action === 'confirm') {
      this.ws.sendCommand('sleep_confirm');
      this.chatMessages.update((msgs) => [
        ...msgs,
        { type: 'status', content: 'Rest up — agent is sleeping...', timestamp: new Date() },
      ]);
    } else {
      this.ws.sendCommand('sleep_deny');
      this.chatMessages.update((msgs) => [
        ...msgs,
        { type: 'status', content: 'Stay awake — agent keeps going.', timestamp: new Date() },
      ]);
    }
  }

  isDrowsyResponded(index: number): boolean {
    return this.drowsyResponded.has(index);
  }

  toggleDriveExpand(index: number): void {
    if (this.expandedDriveActions.has(index)) {
      this.expandedDriveActions.delete(index);
    } else {
      this.expandedDriveActions.add(index);
    }
  }

  isDriveExpanded(index: number): boolean {
    return this.expandedDriveActions.has(index);
  }

  driveActionLabel(actionType: string): string {
    const labels: Record<string, string> = {
      web_search: 'Browsing',
      wikipedia: 'Reading Wikipedia',
      read_page: 'Reading',
      deep_browse: 'Deep Browsing',
      self_test: 'Self-testing',
      self_check: 'Self-checking',
      reach_out: 'Reaching out',
      reflect: 'Reflecting',
    };
    return labels[actionType] || actionType;
  }

  /** Refresh an open editor tab if the agent modified the file. */
  private refreshOpenTabByPath(filePath: string): void {
    if (!filePath) return;
    const idx = this.openTabs().findIndex((t) => t.path === filePath);
    if (idx < 0) return;

    this.fs.readFile(filePath).subscribe({
      next: (res) => {
        this.openTabs.update((tabs) => {
          const newTabs = [...tabs];
          newTabs[idx] = { ...newTabs[idx], content: res.content, modified: false };
          return newTabs;
        });
        // If this is the active tab, update the editor
        if (this.activeTabIndex() === idx) {
          this.setEditorContent(res.content, this.openTabs()[idx].language);
        }
        this.toast.show(`File updated by agent: ${filePath.split(/[/\\]/).pop()}`, 'info', 3000);
      },
    });
  }

  // ── Terminal Resize ────────────────────────────────────────────

  onTerminalResizeStart(event: MouseEvent): void {
    const startY = event.clientY;
    const startHeight = this.terminalHeight();

    const onMove = (e: MouseEvent) => {
      const delta = startY - e.clientY;
      this.terminalHeight.set(Math.max(100, Math.min(600, startHeight + delta)));
      setTimeout(() => this.fitAddon?.fit(), 0);
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  clearTerminal(): void {
    this.terminal?.clear();
  }

  // ── Chat Resize ────────────────────────────────────────────────

  onChatResizeStart(event: MouseEvent): void {
    const startX = event.clientX;
    const startWidth = this.chatWidth();

    const onMove = (e: MouseEvent) => {
      const delta = startX - e.clientX;
      this.chatWidth.set(Math.max(260, Math.min(600, startWidth + delta)));
      setTimeout(() => this.editor?.layout(), 0);
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  // ── Helpers ────────────────────────────────────────────────────

  private detectLanguage(filename: string): string {
    const ext = filename.split('.').pop()?.toLowerCase() || '';
    const map: Record<string, string> = {
      ts: 'TypeScript', js: 'JavaScript', py: 'Python', html: 'HTML',
      css: 'CSS', scss: 'SCSS', json: 'JSON', md: 'Markdown',
      yaml: 'YAML', yml: 'YAML', toml: 'TOML', sh: 'Shell',
      rs: 'Rust', go: 'Go', java: 'Java', cpp: 'C++', c: 'C',
      sql: 'SQL', xml: 'XML', txt: 'Text', jsx: 'JavaScript',
      tsx: 'TypeScript', vue: 'HTML', svelte: 'HTML',
    };
    return map[ext] || 'Text';
  }

  private mapLanguageId(lang: string): string {
    const map: Record<string, string> = {
      TypeScript: 'typescript', JavaScript: 'javascript', Python: 'python',
      HTML: 'html', CSS: 'css', SCSS: 'scss', JSON: 'json',
      Markdown: 'markdown', YAML: 'yaml', Shell: 'shell',
      Rust: 'rust', Go: 'go', Java: 'java', 'C++': 'cpp', C: 'c',
      SQL: 'sql', XML: 'xml', Text: 'plaintext', TOML: 'ini',
    };
    return map[lang] || 'plaintext';
  }

  getFileIcon(node: FileNode): string {
    if (node.isDirectory) return node.expanded ? 'folder-open' : 'folder';
    const ext = node.name.split('.').pop()?.toLowerCase() || '';
    const iconMap: Record<string, string> = {
      ts: 'code', js: 'code', py: 'code', html: 'globe', css: 'palette',
      scss: 'palette', json: 'braces', md: 'book', yml: 'settings',
      yaml: 'settings', sh: 'terminal', rs: 'code', go: 'code',
    };
    return iconMap[ext] || 'file';
  }

  trackByPath(_: number, node: FileNode): string {
    return node.path;
  }

  // ── Tag Parsing (for chat sidebar) ──────────────────────────
  parseTags(content: string): { text: string; tags: SignalTag[] } {
    return parseTags(content);
  }

  stripTags(content: string): string {
    return stripTags(content);
  }

  tagColor(type: string): string {
    return tagColor(type);
  }

  humanType(type: string): string {
    return humanType(type);
  }
}
