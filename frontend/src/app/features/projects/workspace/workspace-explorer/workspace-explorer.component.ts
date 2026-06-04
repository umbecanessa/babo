import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnInit,
  OnChanges,
  SimpleChanges,
  signal,
  ChangeDetectorRef,
  ElementRef,
  ViewChild,
  AfterViewChecked,
  HostListener,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { FilesystemService } from '../../../../core/services/filesystem.service';
import { ExplorerNode } from '../workspace.models';
import { sanitizeWorkspaceEntryName } from '../workspace-path.util';

interface PendingCreate {
  parentDir: string;
  kind: 'file' | 'folder';
  draft: string;
}

interface PendingRename {
  path: string;
  parentDir: string;
  draft: string;
  isDirectory: boolean;
}

interface ContextMenuState {
  x: number;
  y: number;
  node: ExplorerNode;
}

@Component({
  selector: 'app-workspace-explorer',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './workspace-explorer.component.html',
  styleUrl: './workspace-explorer.component.scss',
})
export class WorkspaceExplorerComponent implements OnInit, OnChanges, AfterViewChecked {
  @Input({ required: true }) rootPath = '';
  @Input() activePath = '';
  @Input() shellOpen = false;
  @Input() busy = false;
  @Output() fileOpen = new EventEmitter<string>();
  @Output() uploadFiles = new EventEmitter<void>();
  @Output() uploadFolder = new EventEmitter<void>();
  @Output() toggleShell = new EventEmitter<void>();
  @Output() fileDeleted = new EventEmitter<string>();
  @Output() fileRenamed = new EventEmitter<{ oldPath: string; newPath: string }>();

  @ViewChild('pendingNameInput') pendingNameInput?: ElementRef<HTMLInputElement>;

  tree = signal<ExplorerNode[]>([]);
  loading = signal(false);
  error = signal('');
  selectedDirPath = signal('');
  pendingCreate = signal<PendingCreate | null>(null);
  pendingRename = signal<PendingRename | null>(null);
  contextMenu = signal<ContextMenuState | null>(null);

  private pendingFocus = false;
  private renameFocus = false;

  constructor(
    private fs: FilesystemService,
    private cdr: ChangeDetectorRef,
    private host: ElementRef<HTMLElement>,
  ) {}

  ngOnInit(): void {
    this.loadRoot();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['rootPath'] && !changes['rootPath'].firstChange) {
      this.cancelPendingCreate();
      this.cancelPendingRename();
      this.closeContextMenu();
      this.loadRoot();
      return;
    }
    if (changes['activePath']?.currentValue) {
      void this.revealActivePath();
      this.syncSelectionFromActivePath();
    }
  }

  ngAfterViewChecked(): void {
    if (this.pendingFocus && this.pendingNameInput) {
      this.pendingFocus = false;
      const input = this.pendingNameInput.nativeElement;
      input.focus();
      input.select();
      return;
    }
    if (this.renameFocus) {
      const input = this.host.nativeElement.querySelector(
        '.rename-input',
      ) as HTMLInputElement | null;
      if (input) {
        this.renameFocus = false;
        input.focus();
        input.select();
      }
    }
  }

  refresh(): void {
    this.cancelPendingCreate();
    this.cancelPendingRename();
    this.closeContextMenu();
    this.loadRoot();
  }

  startCreateFile(): void {
    void this.beginPendingCreate('file');
  }

  startCreateFolder(): void {
    void this.beginPendingCreate('folder');
  }

  onNodeClick(node: ExplorerNode, event: Event): void {
    event.stopPropagation();
    this.closeContextMenu();
    if (node.isPending) return;

    if (node.isDirectory) {
      this.selectedDirPath.set(node.path);
      this.toggleDirectory(node);
    } else {
      this.selectedDirPath.set(this.parentDir(node.path));
      this.fileOpen.emit(node.path);
    }
  }

  isSelectedDir(node: ExplorerNode): boolean {
    return node.isDirectory && this.pathsEqual(node.path, this.selectedDirPath());
  }

  isPendingParent(dirPath: string): boolean {
    const pending = this.pendingCreate();
    return !!pending && this.pathsEqual(pending.parentDir, dirPath);
  }

  pendingKind(): 'file' | 'folder' | null {
    return this.pendingCreate()?.kind ?? null;
  }

  updatePendingDraft(value: string): void {
    const pending = this.pendingCreate();
    if (!pending) return;
    this.pendingCreate.set({ ...pending, draft: value });
  }

  onPendingKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.commitPendingCreate();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.cancelPendingCreate();
    }
  }

  onPendingBlur(): void {
    queueMicrotask(() => {
      if (!this.pendingCreate()) return;
      this.commitPendingCreate();
    });
  }

  rootLabel(): string {
    if (!this.rootPath) return 'Workspace';
    const parts = this.rootPath.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || 'Workspace';
  }

  trackNode(_index: number, node: ExplorerNode): string {
    return node.path;
  }

  isActive(node: ExplorerNode): boolean {
    return !node.isDirectory && !!this.activePath && this.pathsEqual(node.path, this.activePath);
  }

  onNodeContextMenu(node: ExplorerNode, event: MouseEvent): void {
    event.preventDefault();
    event.stopPropagation();
    if (node.isPending || this.pendingCreate() || this.pendingRename()) return;

    this.openContextMenu(event.clientX, event.clientY, node);
    if (node.isDirectory) {
      this.selectedDirPath.set(node.path);
    } else {
      this.selectedDirPath.set(this.parentDir(node.path));
    }
  }

  onBodyContextMenu(event: MouseEvent): void {
    if (!this.rootPath) return;
    event.preventDefault();
    event.stopPropagation();
    if (this.pendingCreate() || this.pendingRename()) return;

    this.selectedDirPath.set(this.rootPath);
    this.openContextMenu(event.clientX, event.clientY, {
      name: this.rootLabel(),
      path: this.rootPath,
      isDirectory: true,
      expanded: true,
    });
  }

  isRootNode(node: ExplorerNode): boolean {
    return this.pathsEqual(node.path, this.rootPath);
  }

  private openContextMenu(x: number, y: number, node: ExplorerNode): void {
    const menuWidth = 168;
    const menuHeight = node.isDirectory ? 156 : 88;
    this.contextMenu.set({
      x: Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - menuHeight - 8)),
      node,
    });
  }

  closeContextMenu(): void {
    this.contextMenu.set(null);
  }

  contextMenuRename(): void {
    const menu = this.contextMenu();
    if (!menu || this.isRootNode(menu.node)) return;
    const node = menu.node;
    this.closeContextMenu();
    this.pendingRename.set({
      path: node.path,
      parentDir: this.parentDir(node.path),
      draft: node.name,
      isDirectory: node.isDirectory,
    });
    this.renameFocus = true;
    this.cdr.detectChanges();
  }

  contextMenuDelete(): void {
    const menu = this.contextMenu();
    if (!menu) return;
    const node = menu.node;
    this.closeContextMenu();
    if (this.isRootNode(node)) return;
    this.deleteNode(node);
  }

  contextMenuNewFile(): void {
    const menu = this.contextMenu();
    if (!menu?.node.isDirectory) return;
    this.closeContextMenu();
    this.selectedDirPath.set(menu.node.path);
    void this.beginPendingCreate('file');
  }

  contextMenuNewFolder(): void {
    const menu = this.contextMenu();
    if (!menu?.node.isDirectory) return;
    this.closeContextMenu();
    this.selectedDirPath.set(menu.node.path);
    void this.beginPendingCreate('folder');
  }

  isRenaming(node: ExplorerNode): boolean {
    const pending = this.pendingRename();
    return !!pending && this.pathsEqual(pending.path, node.path);
  }

  updateRenameDraft(value: string): void {
    const pending = this.pendingRename();
    if (!pending) return;
    this.pendingRename.set({ ...pending, draft: value });
  }

  onRenameKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.commitPendingRename();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.cancelPendingRename();
    }
  }

  onRenameBlur(): void {
    queueMicrotask(() => {
      if (!this.pendingRename()) return;
      this.commitPendingRename();
    });
  }

  private commitPendingRename(): void {
    const pending = this.pendingRename();
    if (!pending) return;

    const safe = sanitizeWorkspaceEntryName(pending.draft);
    if (!safe) {
      this.cancelPendingRename();
      return;
    }

    const oldPath = pending.path;
    const newPath = this.fs.joinPath(pending.parentDir, safe);
    this.pendingRename.set(null);

    if (this.pathsEqual(oldPath, newPath)) return;

    this.fs.rename(oldPath, newPath).subscribe({
      next: () => {
        void this.refreshDir(pending.parentDir).then(() => {
          this.fileRenamed.emit({ oldPath, newPath });
        });
      },
      error: () => {
        this.error.set('Failed to rename');
        this.cdr.detectChanges();
      },
    });
  }

  private cancelPendingRename(): void {
    this.pendingRename.set(null);
    this.renameFocus = false;
  }

  private deleteNode(node: ExplorerNode): void {
    this.fs.unlink(node.path).subscribe({
      next: () => {
        void this.refreshDir(this.parentDir(node.path)).then(() => {
          this.fileDeleted.emit(node.path);
        });
      },
      error: () => {
        this.error.set('Failed to delete');
        this.cdr.detectChanges();
      },
    });
  }

  private async beginPendingCreate(kind: 'file' | 'folder'): Promise<void> {
    if (!this.rootPath) return;
    const parentDir = this.resolveCreateParent();
    await this.ensureDirReady(parentDir);

    this.pendingCreate.set({
      parentDir,
      kind,
      draft: kind === 'file' ? 'untitled.txt' : 'newfolder',
    });
    this.pendingFocus = true;
    this.cdr.detectChanges();
    requestAnimationFrame(() => this.scrollPendingIntoView());
  }

  private commitPendingCreate(): void {
    const pending = this.pendingCreate();
    if (!pending) return;

    const safe = sanitizeWorkspaceEntryName(pending.draft);
    if (!safe) {
      this.cancelPendingCreate();
      return;
    }

    const targetPath = this.fs.joinPath(pending.parentDir, safe);
    this.pendingCreate.set(null);

    if (pending.kind === 'file') {
      this.fs.writeFile(targetPath, '').subscribe({
        next: () => {
          void this.refreshDir(pending.parentDir).then(() => {
            this.fileOpen.emit(targetPath);
          });
        },
        error: () => {
          this.error.set('Failed to create file');
          this.cdr.detectChanges();
        },
      });
      return;
    }

    this.fs.mkdir(targetPath, true).subscribe({
      next: () => {
        void this.refreshDir(pending.parentDir).then(() => {
          this.selectedDirPath.set(targetPath);
        });
      },
      error: () => {
        this.error.set('Failed to create folder');
        this.cdr.detectChanges();
      },
    });
  }

  private cancelPendingCreate(): void {
    this.pendingCreate.set(null);
    this.pendingFocus = false;
  }

  private resolveCreateParent(): string {
    const root = this.normalizePath(this.rootPath);
    const selected = this.selectedDirPath();
    if (selected) {
      const node = this.findNodeByPath(selected);
      if (node?.isDirectory) return node.path;
    }

    const active = (this.activePath || '').trim();
    if (active) {
      const norm = this.normalizePath(active);
      if (norm.startsWith(root)) {
        const node = this.findNodeByPath(norm);
        if (node?.isDirectory) return node.path;
        const parent = this.parentDir(norm);
        if (parent && parent.length >= root.length) return parent;
      }
    }

    return this.rootPath;
  }

  private syncSelectionFromActivePath(): void {
    if (!this.activePath || !this.rootPath) return;
    const norm = this.normalizePath(this.activePath);
    const root = this.normalizePath(this.rootPath);
    if (!norm.startsWith(root)) return;

    const node = this.findNodeByPath(norm);
    if (node?.isDirectory) {
      this.selectedDirPath.set(node.path);
      return;
    }
    this.selectedDirPath.set(this.parentDir(norm));
  }

  private async ensureDirReady(dirPath: string): Promise<void> {
    const root = this.normalizePath(this.rootPath);
    const dir = this.normalizePath(dirPath);
    if (this.pathsEqual(dir, root)) {
      if (!this.tree().length && !this.loading()) {
        await this.loadRootAsync();
      }
      return;
    }

    const relative = dir.slice(root.length).replace(/^\//, '');
    const parts = relative.split('/').filter(Boolean);
    let level = this.tree();
    for (const part of parts) {
      const node = level.find((n) => n.isDirectory && n.name === part);
      if (!node) break;
      await this.expandNode(node);
      level = node.children ?? [];
    }
    this.cdr.detectChanges();
  }

  private async refreshDir(dirPath: string): Promise<void> {
    const root = this.normalizePath(this.rootPath);
    const dir = this.normalizePath(dirPath);
    if (this.pathsEqual(dir, root)) {
      await this.loadRootAsync();
      return;
    }

    const node = this.findNodeByPath(dir);
    if (!node?.isDirectory) {
      await this.loadRootAsync();
      return;
    }

    node.loading = true;
    this.cdr.detectChanges();
    await new Promise<void>((resolve) => {
      this.fs.readDir(node.path).subscribe({
        next: (res) => {
          node.children = res.entries.map((e) => ({
            name: e.name,
            path: e.path,
            isDirectory: e.isDirectory,
            expanded: false,
          }));
          node.expanded = true;
          node.loading = false;
          this.cdr.detectChanges();
          resolve();
        },
        error: () => {
          node.loading = false;
          this.cdr.detectChanges();
          resolve();
        },
      });
    });
  }

  private loadRoot(): void {
    void this.loadRootAsync();
  }

  private loadRootAsync(): Promise<void> {
    if (!this.rootPath) {
      this.tree.set([]);
      return Promise.resolve();
    }

    this.loading.set(true);
    this.error.set('');

    return new Promise((resolve) => {
      this.fs.readDir(this.rootPath).subscribe({
        next: (res) => {
          this.tree.set(
            res.entries.map((e) => ({
              name: e.name,
              path: e.path,
              isDirectory: e.isDirectory,
              expanded: false,
            })),
          );
          this.loading.set(false);
          if (!this.selectedDirPath()) {
            this.selectedDirPath.set(this.rootPath);
          }
          if (this.activePath) {
            void this.revealActivePath();
          }
          resolve();
        },
        error: (err) => {
          this.error.set(err?.error?.detail || err?.message || 'Failed to load workspace');
          this.loading.set(false);
          resolve();
        },
      });
    });
  }

  private toggleDirectory(node: ExplorerNode): void {
    node.expanded = !node.expanded;

    if (node.expanded && !node.children?.length) {
      node.loading = true;
      this.cdr.detectChanges();

      this.fs.readDir(node.path).subscribe({
        next: (res) => {
          node.children = res.entries.map((e) => ({
            name: e.name,
            path: e.path,
            isDirectory: e.isDirectory,
            expanded: false,
          }));
          node.loading = false;
          this.cdr.detectChanges();
        },
        error: () => {
          node.loading = false;
          this.cdr.detectChanges();
        },
      });
    }

    this.cdr.detectChanges();
  }

  private findNodeByPath(path: string): ExplorerNode | undefined {
    const target = this.normalizePath(path).toLowerCase();
    const walk = (nodes: ExplorerNode[]): ExplorerNode | undefined => {
      for (const node of nodes) {
        if (this.normalizePath(node.path).toLowerCase() === target) return node;
        if (node.children?.length) {
          const found = walk(node.children);
          if (found) return found;
        }
      }
      return undefined;
    };
    return walk(this.tree());
  }

  private parentDir(filePath: string): string {
    const norm = this.normalizePath(filePath);
    const idx = Math.max(norm.lastIndexOf('/'), norm.lastIndexOf('\\'));
    return idx > 0 ? norm.slice(0, idx) : this.rootPath;
  }

  private pathsEqual(a: string, b: string): boolean {
    return this.normalizePath(a).toLowerCase() === this.normalizePath(b).toLowerCase();
  }

  private normalizePath(path: string): string {
    return path.replace(/\\/g, '/').replace(/\/+$/, '');
  }

  private async revealActivePath(): Promise<void> {
    if (!this.activePath || !this.rootPath) return;

    const root = this.normalizePath(this.rootPath);
    const target = this.normalizePath(this.activePath);
    if (!target.startsWith(root)) return;

    const relative = target.slice(root.length).replace(/^\//, '');
    if (!relative) return;

    const parts = relative.split('/').filter(Boolean);
    if (parts.length === 0) return;

    if (parts.length > 1) {
      let level = this.tree();
      for (let i = 0; i < parts.length - 1; i++) {
        const node = level.find((n) => n.isDirectory && n.name === parts[i]);
        if (!node) return;
        await this.expandNode(node);
        level = node.children ?? [];
      }
    }

    this.cdr.detectChanges();
    requestAnimationFrame(() => this.scrollActiveIntoView());
  }

  private expandNode(node: ExplorerNode): Promise<void> {
    node.expanded = true;
    if (node.children?.length) {
      this.cdr.detectChanges();
      return Promise.resolve();
    }

    node.loading = true;
    this.cdr.detectChanges();

    return new Promise((resolve) => {
      this.fs.readDir(node.path).subscribe({
        next: (res) => {
          node.children = res.entries.map((e) => ({
            name: e.name,
            path: e.path,
            isDirectory: e.isDirectory,
            expanded: false,
          }));
          node.loading = false;
          this.cdr.detectChanges();
          resolve();
        },
        error: () => {
          node.loading = false;
          this.cdr.detectChanges();
          resolve();
        },
      });
    });
  }

  private scrollActiveIntoView(): void {
    const row = this.host.nativeElement.querySelector('.tree-row.active');
    row?.scrollIntoView({ block: 'nearest' });
  }

  private scrollPendingIntoView(): void {
    const row = this.host.nativeElement.querySelector('.tree-row.pending-create');
    row?.scrollIntoView({ block: 'nearest' });
  }

  @HostListener('document:click')
  onDocumentClick(): void {
    this.closeContextMenu();
  }

  @HostListener('document:keydown.escape')
  onEscapeKey(): void {
    this.closeContextMenu();
  }
}
