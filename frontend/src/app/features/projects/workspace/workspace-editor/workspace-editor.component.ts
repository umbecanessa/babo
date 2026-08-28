import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnChanges,
  SimpleChanges,
  ElementRef,
  ViewChild,
  HostListener,
  AfterViewInit,
  OnDestroy,
  inject,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { Compartment, EditorState, Extension } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from '@codemirror/commands';
import { highlightSelectionMatches } from '@codemirror/search';
import { baboTheme, languageExtension } from '../codemirror.loader';
import { EditorTab } from '../workspace.models';
import { languageFromFileName } from '../language.util';
import { workspacePathsEqual } from '../workspace-path.util';
import { ThemeService } from '../../../../core/services/theme.service';

@Component({
  selector: 'app-workspace-editor',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  templateUrl: './workspace-editor.component.html',
  styleUrl: './workspace-editor.component.scss',
})
export class WorkspaceEditorComponent
  implements OnChanges, AfterViewInit, OnDestroy
{
  @ViewChild('editorHost') editorHost?: ElementRef<HTMLDivElement>;

  @Input({ required: true }) tabs: EditorTab[] = [];
  @Input({ required: true }) activePath = '';
  @Input() shellOpen = false;

  @Output() tabSelect = new EventEmitter<string>();
  @Output() tabClose = new EventEmitter<string>();
  @Output() save = new EventEmitter<string>();

  private readonly themeService = inject(ThemeService);
  private readonly themeCompartment = new Compartment();
  private readonly editableCompartment = new Compartment();

  private view?: EditorView;
  private readonly states = new Map<string, EditorState>();
  private readonly dirtyPaths = new Set<string>();
  private readonly pendingStates = new Set<string>();
  private resizeObserver?: ResizeObserver;
  private editorReady = false;

  constructor() {
    effect(() => {
      this.themeService.effective();
      if (this.editorReady) {
        this.applyEditorTheme();
      }
    });
  }

  ngAfterViewInit(): void {
    void this.initEditor();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.editorReady) return;
    if (changes['shellOpen']?.currentValue === true) {
      this.view?.contentDOM.blur();
      return;
    }
    if (changes['activePath']) {
      void this.syncStates().then(() => this.showActiveState());
      return;
    }
    if (changes['tabs'] && this.tabsStructurallyChanged(changes['tabs'])) {
      void this.syncStates().then(() => this.showActiveState());
    }
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.view?.destroy();
    this.view = undefined;
    this.states.clear();
    this.editorReady = false;
  }

  @HostListener('window:keydown', ['$event'])
  onKeydown(event: KeyboardEvent): void {
    if ((event.ctrlKey || event.metaKey) && event.key === 's') {
      event.preventDefault();
      if (this.activePath) {
        this.save.emit(this.activePath);
      }
    }
  }

  selectTab(path: string, event?: Event): void {
    event?.stopPropagation();
    this.tabSelect.emit(path);
  }

  closeTab(path: string, event: Event): void {
    event.stopPropagation();
    this.tabClose.emit(path);
  }

  activeTab(): EditorTab | null {
    return this.tabs.find((t) => t.path === this.activePath) ?? null;
  }

  isDirty(path: string): boolean {
    return this.dirtyPaths.has(path);
  }

  getContent(path: string): string {
    for (const [key, state] of this.states) {
      if (workspacePathsEqual(key, path)) {
        return state.doc.toString();
      }
    }
    return this.tabs.find((t) => workspacePathsEqual(t.path, path))?.content ?? '';
  }

  markSaved(path: string, content?: string): void {
    const state = this.states.get(path);
    const next = content ?? state?.doc.toString() ?? '';
    if (state && state.doc.toString() !== next) {
      this.states.set(
        path,
        state.update({ changes: { from: 0, to: state.doc.length, insert: next } })
          .state,
      );
      if (workspacePathsEqual(this.activePath, path) && this.view) {
        this.view.setState(this.states.get(path)!);
        this.applyEditorTheme();
      }
    }
    this.dirtyPaths.delete(path);
  }

  /** Preserve open buffer when explorer renames a file. */
  async renameTabPath(
    oldPath: string,
    newPath: string,
    fileName: string,
  ): Promise<void> {
    if (workspacePathsEqual(oldPath, newPath)) return;

    const state = this.states.get(oldPath);
    const wasDirty = this.dirtyPaths.has(oldPath);
    this.states.delete(oldPath);
    this.dirtyPaths.delete(oldPath);
    this.pendingStates.delete(oldPath);

    if (!state) return;

    const lang = await languageExtension(fileName, languageFromFileName(fileName));
    const newState = EditorState.create({
      doc: state.doc.toString(),
      extensions: this.stateExtensions(newPath, true, lang),
    });
    this.states.set(newPath, newState);
    if (wasDirty) {
      this.dirtyPaths.add(newPath);
    }

    if (workspacePathsEqual(this.activePath, oldPath) && this.view) {
      this.mountState(newState);
    }
  }

  private isDark(): boolean {
    return this.themeService.effective() === 'dark';
  }

  private baseExtensions(): Extension[] {
    return [
      history(),
      keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
      highlightSelectionMatches(),
      this.themeCompartment.of(baboTheme(this.isDark())),
      EditorView.updateListener.of((update) => {
        if (!update.docChanged || !this.view || update.view !== this.view) return;
        const active = this.activePath;
        if (!active) return;
        this.states.set(active, update.state);
        this.dirtyPaths.add(active);
      }),
    ];
  }

  private stateExtensions(
    path: string,
    editable: boolean,
    lang: Extension = [],
  ): Extension[] {
    return [
      ...this.baseExtensions(),
      this.editableCompartment.of(EditorView.editable.of(editable)),
      lang,
    ];
  }

  private async initEditor(): Promise<void> {
    const host = this.editorHost?.nativeElement;
    if (!host) return;

    try {
      this.view = new EditorView({
        parent: host,
        state: EditorState.create({
          doc: '',
          extensions: this.stateExtensions('', false),
        }),
      });

      this.resizeObserver = new ResizeObserver(() => {
        requestAnimationFrame(() => this.view?.requestMeasure());
      });
      this.resizeObserver.observe(host);
      const main = host.closest('.editor-main');
      if (main) this.resizeObserver.observe(main);

      this.editorReady = true;
      await this.syncStates();
      this.showActiveState();
    } catch (err) {
      console.error('CodeMirror editor failed to load', err);
    }
  }

  private applyEditorTheme(): void {
    if (!this.view) return;
    this.view.dispatch({
      effects: this.themeCompartment.reconfigure(baboTheme(this.isDark())),
    });
  }

  private setEditable(editable: boolean): void {
    if (!this.view) return;
    this.view.dispatch({
      effects: this.editableCompartment.reconfigure(
        EditorView.editable.of(editable),
      ),
    });
  }

  private async syncStates(): Promise<void> {
    const open = new Set(this.tabs.map((t) => t.path));

    for (const path of [...this.states.keys()]) {
      if (!open.has(path)) {
        this.states.delete(path);
        this.dirtyPaths.delete(path);
        this.pendingStates.delete(path);
      }
    }

    await Promise.all(
      this.tabs.filter((t) => !t.loading).map((tab) => this.ensureState(tab)),
    );
  }

  private async ensureState(tab: EditorTab): Promise<EditorState> {
    const existing = this.states.get(tab.path);
    if (existing) {
      if (
        !this.dirtyPaths.has(tab.path) &&
        existing.doc.toString() !== tab.content
      ) {
        const updated = existing.update({
          changes: {
            from: 0,
            to: existing.doc.length,
            insert: tab.content,
          },
        }).state;
        this.states.set(tab.path, updated);
        return updated;
      }
      return existing;
    }

    if (this.pendingStates.has(tab.path)) {
      return (
        this.states.get(tab.path) ??
        EditorState.create({
          doc: tab.content,
          extensions: this.stateExtensions(tab.path, true),
        })
      );
    }

    this.pendingStates.add(tab.path);
    const lang = await languageExtension(tab.name, tab.language);
    const state = EditorState.create({
      doc: tab.content,
      extensions: this.stateExtensions(tab.path, true, lang),
    });
    this.states.set(tab.path, state);
    this.pendingStates.delete(tab.path);
    return state;
  }

  private showActiveState(): void {
    if (!this.view) return;

    const tab = this.activeTab();
    if (!tab || tab.loading || !this.activePath) {
      this.setEditable(false);
      return;
    }

    const state = this.states.get(tab.path);
    if (!state) {
      void this.ensureState(tab).then((s) => {
        if (this.activePath === tab.path) {
          this.mountState(s);
        }
      });
      return;
    }

    this.mountState(state);
  }

  private mountState(state: EditorState): void {
    if (!this.view) return;
    this.view.setState(state);
    this.applyEditorTheme();
    this.setEditable(true);
    if (!this.shellOpen) {
      this.view.focus();
    }
    requestAnimationFrame(() => this.view?.requestMeasure());
  }

  /** Ignore parent tab updates that only flip the dirty flag. */
  private tabsStructurallyChanged(change: SimpleChanges['tabs']): boolean {
    const prev = (change.previousValue ?? []) as EditorTab[];
    const cur = (change.currentValue ?? []) as EditorTab[];
    if (prev.length !== cur.length) return true;

    const prevByPath = new Map(
      prev.map((t) => [normalizeTabPath(t.path), t]),
    );
    for (const tab of cur) {
      const key = normalizeTabPath(tab.path);
      const before = prevByPath.get(key);
      if (!before) {
        if (this.isRenamedTab(prev, cur, tab)) continue;
        return true;
      }
      if (before.loading !== tab.loading) return true;
      if (before.content !== tab.content && !this.dirtyPaths.has(tab.path)) {
        return true;
      }
    }
    return false;
  }

  /** Path-only rename already handled via renameTabPath(). */
  private isRenamedTab(prev: EditorTab[], cur: EditorTab[], tab: EditorTab): boolean {
    const removed = prev.filter(
      (p) => !cur.some((c) => workspacePathsEqual(c.path, p.path)),
    );
    if (removed.length !== 1) return false;
    const added = cur.filter(
      (c) => !prev.some((p) => workspacePathsEqual(p.path, c.path)),
    );
    if (added.length !== 1 || added[0].path !== tab.path) return false;
    return this.states.has(tab.path);
  }
}

function normalizeTabPath(path: string): string {
  return path.replace(/\\/g, '/').toLowerCase();
}
