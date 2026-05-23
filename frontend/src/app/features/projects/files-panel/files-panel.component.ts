import { Component, Input, OnInit, OnChanges, OnDestroy, SimpleChanges, signal, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription } from 'rxjs';
import { ApiService } from '../../../core/services/api.service';

interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children?: FileNode[];
  expanded?: boolean;
  size?: string;
  modified?: string;
  _uid: number;
}

@Component({
  selector: 'app-files-panel',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './files-panel.component.html',
  styleUrl: './files-panel.component.scss',
})
export class FilesPanelComponent implements OnInit, OnChanges, OnDestroy {
  @Input() agentId = '';
  @Input() workspacePath = '';

  tree = signal<FileNode[]>([]);
  loading = signal(false);
  error = signal('');

  previewPath = signal('');
  previewContent = signal('');
  previewLoading = signal(false);

  private treeSub?: Subscription;
  private _uidSeq = 0;

  constructor(private api: ApiService, private cdr: ChangeDetectorRef) {}

  ngOnInit(): void {
    this.loadTree();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (
      (changes['workspacePath'] || changes['agentId']) &&
      !(changes['workspacePath']?.firstChange && changes['agentId']?.firstChange)
    ) {
      this.loadTree();
    }
  }

  ngOnDestroy(): void {
    this.treeSub?.unsubscribe();
  }

  loadTree(): void {
    const root = this.workspacePath || this.resolveWorkspacePath();
    if (!root) return;
    this.treeSub?.unsubscribe();
    this.loading.set(true);
    this.error.set('');

    this.treeSub = this.api.getFileTree(root, 3).subscribe({
      next: (res) => {
        this.tree.set(this.parseTreeText(res.text || '', root));
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail || 'Failed to load file tree');
        this.loading.set(false);
      },
    });
  }

  toggleDir(node: FileNode): void {
    if (node.type !== 'dir') return;
    node.expanded = !node.expanded;

    if (node.expanded && (!node.children || node.children.length === 0)) {
      this.api.getFileTree(node.path, 2).subscribe({
        next: (res) => {
          node.children = this.parseTreeText(res.text || '', node.path);
          this.cdr.detectChanges();
        },
      });
    }
    this.cdr.detectChanges();
  }

  openFile(node: FileNode): void {
    if (node.type !== 'file') return;
    this.previewPath.set(node.path);
    this.previewLoading.set(true);
    this.previewContent.set('');

    const normalizedPath = node.path.replace(/\\/g, '/');
    this.api.readFile(normalizedPath, 0, 200).subscribe({
      next: (res) => {
        this.previewContent.set(res.content || '(empty)');
        this.previewLoading.set(false);
      },
      error: (err) => {
        const detail = err?.error?.detail || err?.message || 'unknown error';
        this.previewContent.set(`Cannot open file: ${detail}`);
        this.previewLoading.set(false);
      },
    });
  }

  closePreview(): void {
    this.previewPath.set('');
    this.previewContent.set('');
  }

  fileIcon(name: string): string {
    if (name.endsWith('.py')) return '\uD83D\uDC0D';
    if (name.endsWith('.ts') || name.endsWith('.js')) return '\u26A1';
    if (name.endsWith('.json')) return '\u2699';
    if (name.endsWith('.md')) return '\uD83D\uDCDD';
    if (name.endsWith('.html')) return '\uD83C\uDF10';
    if (name.endsWith('.css') || name.endsWith('.scss')) return '\uD83C\uDFA8';
    if (name.endsWith('.env')) return '\uD83D\uDD12';
    if (name.endsWith('.sql')) return '\uD83D\uDDC4';
    return '\uD83D\uDCC4';
  }

  private resolveWorkspacePath(): string {
    if (this.agentId) {
      const nls = (window as any).nls;
      if (nls?.getDataPath) {
        return `${nls.getDataPath()}\\agents\\${this.agentId}\\workspace`;
      }
      return `C:\\Users\\umber\\AppData\\Roaming\\babo-desktop\\data\\agents\\${this.agentId}\\workspace`;
    }
    return '';
  }

  private parseTreeText(text: string, rootPath: string): FileNode[] {
    const root: FileNode[] = [];
    const lines = text.split('\n');
    const sep = rootPath.includes('/') ? '/' : '\\';
    const stack: { depth: number; children: FileNode[] }[] = [{ depth: -1, children: root }];

    for (const line of lines) {
      if (!line.trim()) continue;

      const stripped = line.replace(/^[\s│├└─]+/, '').trim();
      if (!stripped || stripped.startsWith('Directory:') || stripped.startsWith('---')
          || stripped.startsWith('(')) continue;

      const leadingSpaces = line.search(/\S/);
      const depth = Math.max(0, Math.floor(leadingSpaces / 2));

      const sizeMatch = stripped.match(/\s+\(([\d.]+\s?(?:B|KB|MB|GB))\)$/);
      const cleanName = sizeMatch ? stripped.slice(0, sizeMatch.index) : stripped;
      const isDir = cleanName.endsWith('/') || cleanName.endsWith('\\');
      const name = cleanName.replace(/[/\\]$/, '');
      if (!name || name === '.' || name === '..') continue;

      while (stack.length > 1 && stack[stack.length - 1].depth >= depth) {
        stack.pop();
      }

      const parentDir = stack[stack.length - 1];
      const parentPath = stack.length <= 1
        ? rootPath
        : (parentDir as any)._path || rootPath;

      const fullPath = `${parentPath}${sep}${name}`;

      const node: FileNode = {
        name,
        path: fullPath,
        type: isDir ? 'dir' : 'file',
        children: isDir ? [] : undefined,
        expanded: isDir,
        size: sizeMatch ? sizeMatch[1] : undefined,
        _uid: ++this._uidSeq,
      };
      parentDir.children.push(node);

      if (isDir) {
        stack.push({ depth, children: node.children!, _path: fullPath } as any);
      }
    }

    return root;
  }
}
