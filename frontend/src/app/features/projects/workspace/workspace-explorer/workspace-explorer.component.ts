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
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FilesystemService } from '../../../../core/services/filesystem.service';
import { ExplorerNode } from '../workspace.models';

@Component({
  selector: 'app-workspace-explorer',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './workspace-explorer.component.html',
  styleUrl: './workspace-explorer.component.scss',
})
export class WorkspaceExplorerComponent implements OnInit, OnChanges {
  @Input({ required: true }) rootPath = '';
  @Input() activePath = '';
  @Input() shellOpen = false;
  @Input() busy = false;
  @Output() fileOpen = new EventEmitter<string>();
  @Output() createFile = new EventEmitter<void>();
  @Output() createFolder = new EventEmitter<void>();
  @Output() uploadFiles = new EventEmitter<void>();
  @Output() uploadFolder = new EventEmitter<void>();
  @Output() toggleShell = new EventEmitter<void>();

  tree = signal<ExplorerNode[]>([]);
  loading = signal(false);
  error = signal('');

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
      this.loadRoot();
      return;
    }
    if (changes['activePath']?.currentValue) {
      void this.revealActivePath();
    }
  }

  refresh(): void {
    this.loadRoot();
  }

  onNodeClick(node: ExplorerNode, event: Event): void {
    event.stopPropagation();
    if (node.isDirectory) {
      this.toggleDirectory(node);
    } else {
      this.fileOpen.emit(node.path);
    }
  }

  rootLabel(): string {
    if (!this.rootPath) return 'Workspace';
    const parts = this.rootPath.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || 'Workspace';
  }

  private loadRoot(): void {
    if (!this.rootPath) {
      this.tree.set([]);
      return;
    }

    this.loading.set(true);
    this.error.set('');

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
        if (this.activePath) {
          void this.revealActivePath();
        }
      },
      error: (err) => {
        this.error.set(err?.error?.detail || err?.message || 'Failed to load workspace');
        this.loading.set(false);
      },
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

  trackNode(_index: number, node: ExplorerNode): string {
    return node.path;
  }

  isActive(node: ExplorerNode): boolean {
    return !node.isDirectory && !!this.activePath && this.pathsEqual(node.path, this.activePath);
  }

  private pathsEqual(a: string, b: string): boolean {
    return this.normalizePath(a).toLowerCase() === this.normalizePath(b).toLowerCase();
  }

  private normalizePath(path: string): string {
    return path.replace(/\\/g, '/').replace(/\/+$/, '');
  }

  private async revealActivePath(): Promise<void> {
    if (!this.activePath || !this.rootPath || this.tree().length === 0) return;

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
}
