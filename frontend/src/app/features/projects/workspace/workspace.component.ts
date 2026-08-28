import {
  Component,
  Input,
  OnChanges,
  OnInit,
  SimpleChanges,
  ViewChild,
  signal,
  computed,
  HostListener,
  ElementRef,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FilesystemService } from '../../../core/services/filesystem.service';
import { ToastService } from '../../../shared/toast/toast.service';
import { WorkspaceExplorerComponent } from './workspace-explorer/workspace-explorer.component';
import { WorkspaceEditorComponent } from './workspace-editor/workspace-editor.component';
import { WorkspaceTerminalComponent } from './workspace-terminal/workspace-terminal.component';
import { EditorTab } from './workspace.models';
import { AgentWorkspaceContextService } from '../../../core/services/agent-workspace-context.service';
import { WorkspaceTerminalTabsService } from '../../../core/services/workspace-terminal-tabs.service';
import { WorkspaceNavService } from '../../../core/services/workspace-nav.service';
import {
  buildWorkspaceFilePathCandidates,
  expandWorkspaceFilePathCandidates,
  isAbsoluteFilesystemPath,
  isPathUnderWorkspace,
  migrateWorkspacePath,
  resolveAgentWorkspacePath,
  workspacePathsEqual,
} from './workspace-path.util';
import { languageFromFileName } from './language.util';

@Component({
  selector: 'app-workspace',
  standalone: true,
  imports: [CommonModule, WorkspaceExplorerComponent, WorkspaceEditorComponent, WorkspaceTerminalComponent],
  templateUrl: './workspace.component.html',
  styleUrl: './workspace.component.scss',
})
export class WorkspaceComponent implements OnInit, OnChanges {
  @Input({ required: true }) agentId = '';
  @Input() workspacePath = '';
  @Input() initialFilePath = '';

  readonly agentIdSignal = signal('');

  @ViewChild(WorkspaceEditorComponent) editorPanel?: WorkspaceEditorComponent;
  @ViewChild(WorkspaceExplorerComponent) explorerPanel?: WorkspaceExplorerComponent;
  @ViewChild('fileUploadInput') fileUploadInput?: ElementRef<HTMLInputElement>;
  @ViewChild('folderUploadInput') folderUploadInput?: ElementRef<HTMLInputElement>;

  rootPath = signal('');
  tabs = signal<EditorTab[]>([]);
  activePath = signal('');
  explorerWidth = signal(260);
  uploading = signal(false);

  private readonly terminalTabs = inject(WorkspaceTerminalTabsService);
  private readonly workspaceNav = inject(WorkspaceNavService);
  shellOpen = computed(() => this.terminalTabs.panelOpen(this.agentIdSignal())());
  terminalTabsList = computed(() => this.terminalTabs.tabs(this.agentIdSignal())());
  activeTerminalTabId = computed(() => this.terminalTabs.activeTabId(this.agentIdSignal())());

  private resizing = false;
  private resizeStartX = 0;
  private resizeStartWidth = 0;

  constructor(
    private fs: FilesystemService,
    private toast: ToastService,
    private workspaceCtx: AgentWorkspaceContextService,
  ) {}

  ngOnInit(): void {
    this.agentIdSignal.set(this.agentId);
    const root = this.workspacePath || resolveAgentWorkspacePath(this.agentId);
    this.rootPath.set(root);
    this.workspaceCtx.hydrateAgent(this.agentId);
    this._openInitialFileIfNeeded();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['agentId']) {
      this.agentIdSignal.set(this.agentId);
      const root = this.workspacePath || resolveAgentWorkspacePath(this.agentId);
      if (root) {
        this.rootPath.set(root);
      }
      this.workspaceCtx.hydrateAgent(this.agentId);
    }
    if (changes['initialFilePath']) {
      this._openInitialFileIfNeeded();
    }
  }

  private _openInitialFileIfNeeded(): void {
    const path = (this.initialFilePath || '').trim();
    if (!path) return;
    queueMicrotask(() => this.openFile(path));
  }

  @HostListener('document:mousemove', ['$event'])
  onResizeMove(event: MouseEvent): void {
    if (!this.resizing) return;
    const delta = event.clientX - this.resizeStartX;
    const next = Math.min(480, Math.max(180, this.resizeStartWidth + delta));
    this.explorerWidth.set(next);
  }

  @HostListener('document:mouseup')
  onResizeEnd(): void {
    this.resizing = false;
  }

  onResizeStart(event: MouseEvent): void {
    event.preventDefault();
    this.resizing = true;
    this.resizeStartX = event.clientX;
    this.resizeStartWidth = this.explorerWidth();
  }

  openFile(path: string): void {
    const root = this.rootPath().replace(/\\/g, '/').replace(/\/+$/, '');
    const trimmed = (path || '').trim();
    const projectDir = this.workspaceCtx.getProjectDir(this.agentId);

    if (isAbsoluteFilesystemPath(trimmed) && root) {
      const abs = trimmed.replace(/\\/g, '/');
      const absLower = abs.toLowerCase();
      const rootLower = root.toLowerCase();
      if (absLower.startsWith(`${rootLower}/`)) {
        this._openFileCandidate([abs], 0);
        return;
      }
    }

    const openCandidates = (projectDirs: string[]) => {
      const candidates = expandWorkspaceFilePathCandidates(
        root,
        path,
        projectDir,
        projectDir ? [] : projectDirs,
      );
      if (!candidates.length) {
        this.toast.show('Invalid file path', 'error');
        return;
      }
      this._openFileCandidate(candidates, 0);
    };

    if (projectDir || !root) {
      openCandidates([]);
      return;
    }

    this.fs.readDir(root).subscribe({
      next: (res) => {
        const dirs = (res.entries || [])
          .filter((e) => e.isDirectory && !e.name.startsWith('.'))
          .map((e) => e.name);
        openCandidates(dirs);
      },
      error: () => openCandidates([]),
    });
  }

  private _openFileCandidate(candidates: string[], index: number): void {
    if (index >= candidates.length) {
      this.toast.show('File not found in workspace', 'error');
      return;
    }
    const absPath = candidates[index];

    this.fs.stat(absPath).subscribe({
      next: (info) => {
        if (info.isDirectory) {
          this.activePath.set(absPath);
          return;
        }
        if (!info.isFile) {
          if (index + 1 < candidates.length) {
            this._openFileCandidate(candidates, index + 1);
            return;
          }
          this.toast.show('Path is not a file', 'error');
          return;
        }
        this._readFileAt(absPath, candidates, index);
      },
      error: (err) => {
        const detail = String(err?.error?.detail || err?.message || '');
        const missing =
          detail.includes('ENOENT')
          || detail.toLowerCase().includes('no such file');
        if (missing && index + 1 < candidates.length) {
          this._openFileCandidate(candidates, index + 1);
          return;
        }
        this.toast.show(detail || 'Failed to open path', 'error');
      },
    });
  }

  private _readFileAt(absPath: string, candidates: string[], index: number): void {
    this._rememberProjectDirFromAbsolute(absPath);
    const existing = this.tabs().find(
      (t) =>
        workspacePathsEqual(t.path, absPath)
        || candidates.some((c) => workspacePathsEqual(c, t.path)),
    );
    if (existing) {
      this.activePath.set(existing.path);
      return;
    }

    const name = absPath.split(/[/\\]/).pop() || absPath;
    const language = languageFromFileName(name);
    const loadingTab: EditorTab = {
      path: absPath,
      name,
      language,
      content: '',
      dirty: false,
      loading: true,
    };

    this.tabs.update((tabs) => [...tabs, loadingTab]);
    this.activePath.set(absPath);

    this.fs.readFile(absPath).subscribe({
      next: (res) => {
        this.tabs.update((tabs) =>
          tabs.map((t) =>
            workspacePathsEqual(t.path, absPath)
              ? { ...t, content: res.content, loading: false }
              : t,
          ),
        );
      },
      error: (err) => {
        const detail = String(err?.error?.detail || err?.message || '');
        const isDir =
          detail.includes('EISDIR')
          || detail.toLowerCase().includes('illegal operation on a directory');
        this.tabs.update((tabs) => tabs.filter((t) => t.path !== absPath));
        if (isDir) {
          this.activePath.set(absPath);
          return;
        }
        const missing =
          detail.includes('ENOENT')
          || detail.toLowerCase().includes('no such file');
        if (this.activePath() === absPath) {
          this.activePath.set('');
        }
        if (missing && index + 1 < candidates.length) {
          this._openFileCandidate(candidates, index + 1);
          return;
        }
        this.toast.show(detail || 'Failed to read file', 'error');
      },
    });
  }

  /** Learn project_dir from a resolved absolute path under the workspace root. */
  private _rememberProjectDirFromAbsolute(absPath: string): void {
    const root = this.rootPath().replace(/\\/g, '/').replace(/\/+$/, '');
    const norm = absPath.replace(/\\/g, '/');
    if (!root || !norm.toLowerCase().startsWith(`${root.toLowerCase()}/`)) return;
    const rel = norm.slice(root.length).replace(/^\/+/, '');
    const first = rel.split('/')[0];
    if (first && !first.startsWith('.')) {
      this.workspaceCtx.setProjectDir(this.agentId, first);
    }
  }

  selectTab(path: string): void {
    this.activePath.set(path);
  }

  closeTab(path: string): void {
    this.tabs.update((tabs) => tabs.filter((t) => !workspacePathsEqual(t.path, path)));
    if (workspacePathsEqual(this.activePath(), path)) {
      const remaining = this.tabs();
      this.activePath.set(remaining.length ? remaining[remaining.length - 1].path : '');
    }
  }

  onFileDeleted(path: string): void {
    this.tabs.update((tabs) =>
      tabs.filter((t) => !isPathUnderWorkspace(t.path, path)),
    );
    if (isPathUnderWorkspace(this.activePath(), path)) {
      const remaining = this.tabs();
      this.activePath.set(remaining.length ? remaining[remaining.length - 1].path : '');
    }
  }

  onFileRenamed(event: { oldPath: string; newPath: string }): void {
    const { oldPath, newPath } = event;

    void (async () => {
      const affected = this.tabs().filter((t) => isPathUnderWorkspace(t.path, oldPath));
      for (const tab of affected) {
        const migratedPath = migrateWorkspacePath(oldPath, newPath, tab.path);
        const name = migratedPath.split(/[/\\]/).pop() || tab.name;
        await this.editorPanel?.renameTabPath(tab.path, migratedPath, name);
      }

      this.tabs.update((tabs) =>
        tabs.map((t) => {
          if (!isPathUnderWorkspace(t.path, oldPath)) return t;
          const migratedPath = migrateWorkspacePath(oldPath, newPath, t.path);
          const name = migratedPath.split(/[/\\]/).pop() || t.name;
          return {
            ...t,
            path: migratedPath,
            name,
            language: languageFromFileName(name),
          };
        }),
      );

      if (isPathUnderWorkspace(this.activePath(), oldPath)) {
        this.activePath.set(
          migrateWorkspacePath(oldPath, newPath, this.activePath()),
        );
      }
    })();
  }

  saveTab(path: string): void {
    const tab = this.tabs().find((t) => workspacePathsEqual(t.path, path));
    if (!tab) return;

    const content = this.editorPanel?.getContent(tab.path) ?? tab.content;

    this.fs.writeFile(tab.path, content).subscribe({
      next: () => {
        this.editorPanel?.markSaved(tab.path, content);
        this.tabs.update((tabs) =>
          tabs.map((t) =>
            workspacePathsEqual(t.path, tab.path)
              ? { ...t, dirty: false, content }
              : t,
          ),
        );
        this.toast.show('Saved', 'info');
      },
      error: (err) => {
        const detail = err?.error?.detail || err?.message || 'Failed to save file';
        this.toast.show(detail, 'error');
      },
    });
  }

  toggleShell(): void {
    const wasOpen = this.shellOpen();
    const nowOpen = this.terminalTabs.togglePanel(this.agentIdSignal());
    if (nowOpen && !wasOpen) {
      (document.activeElement as HTMLElement | null)?.blur?.();
      void this.workspaceNav.syncTerminalQuery(this.agentIdSignal(), true);
    } else if (!nowOpen && wasOpen) {
      void this.workspaceNav.syncTerminalQuery(this.agentIdSignal(), false);
    }
  }

  selectTerminalTab(tabId: string): void {
    this.terminalTabs.selectTab(this.agentIdSignal(), tabId);
  }

  addTerminalTab(): void {
    this.terminalTabs.addStandaloneTab(this.agentIdSignal());
  }

  closeTerminalTab(tabId: string): void {
    const stillOpen = this.terminalTabs.closeTab(this.agentIdSignal(), tabId);
    if (!stillOpen) {
      void this.workspaceNav.syncTerminalQuery(this.agentIdSignal(), false);
    }
  }

  triggerFileUpload(): void {
    this.fileUploadInput?.nativeElement.click();
  }

  triggerFolderUpload(): void {
    this.folderUploadInput?.nativeElement.click();
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files ? Array.from(input.files) : [];
    input.value = '';
    if (!files.length) return;
    this.uploadSelectedFiles(files);
  }

  private uploadSelectedFiles(files: File[]): void {
    const root = this.rootPath();
    if (!root) return;

    this.uploading.set(true);
    this.fs.uploadFiles(root, files).subscribe({
      next: () => {
        this.uploading.set(false);
        this.explorerPanel?.refresh();
        this.toast.show(`Uploaded ${files.length} item${files.length === 1 ? '' : 's'}`, 'info');
      },
      error: (err) => {
        this.uploading.set(false);
        this.toast.show(err?.error?.detail || err?.message || 'Upload failed', 'error');
      },
    });
  }
}
