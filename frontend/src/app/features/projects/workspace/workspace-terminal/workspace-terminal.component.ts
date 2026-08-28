import {
  Component,
  Input,
  Output,
  EventEmitter,
  AfterViewInit,
  OnDestroy,
  OnChanges,
  SimpleChanges,
  ElementRef,
  ViewChild,
  HostListener,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import { Subscription } from 'rxjs';
import { TerminalService, TerminalOutput } from '../../../../core/services/terminal.service';
import { TerminalConnection } from '../../../../core/services/terminal-connection';
import { PlatformService } from '../../../../core/services/platform.service';
import { WorkspaceTerminalTabsService } from '../../../../core/services/workspace-terminal-tabs.service';

@Component({
  selector: 'app-workspace-terminal',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './workspace-terminal.component.html',
  styleUrl: './workspace-terminal.component.scss',
})
export class WorkspaceTerminalComponent implements AfterViewInit, OnDestroy, OnChanges {
  @Input() cwd = '';
  @Input() agentId = '';
  @Input() workspacePath = '';
  @Input() mirrorAgent = false;
  @Input() title = 'Shell';
  @Output() closed = new EventEmitter<void>();
  @ViewChild('host') host!: ElementRef<HTMLDivElement>;

  private term?: Terminal;
  private fitAddon?: FitAddon;
  private outputSub?: Subscription;
  private resizeObserver?: ResizeObserver;
  private cwdApplied = false;
  private connection?: TerminalConnection;

  private readonly terminalService = inject(TerminalService);
  private readonly platform = inject(PlatformService);
  readonly terminalTabs = inject(WorkspaceTerminalTabsService);

  ngAfterViewInit(): void {
    const mirrorBlocked = this.mirrorAgent && !this.terminalTabs.agentMirrorSupported;

    this.term = new Terminal({
      cursorBlink: !this.mirrorAgent || mirrorBlocked,
      disableStdin: this.mirrorAgent && !mirrorBlocked,
      fontSize: 12,
      fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
      theme: {
        background: '#0d0f14',
        foreground: '#e2e8f0',
        cursor: '#818cf8',
      },
      scrollback: 5000,
    });

    this.fitAddon = new FitAddon();
    this.term.loadAddon(this.fitAddon);
    this.term.loadAddon(new WebLinksAddon());
    this.term.open(this.host.nativeElement);
    this.fitAddon.fit();

    if (mirrorBlocked) {
      this.term.writeln(
        '\r\n\x1b[33mAgent shell mirror requires the Babo desktop app.\x1b[0m',
      );
      this.term.writeln(
        '\x1b[90mUse a standalone terminal tab here, or open Babo Desktop to watch the agent shell.\x1b[0m\r\n',
      );
      return;
    }

    this.connection = this.terminalService.createConnection();

    this.outputSub = this.connection.onOutput().subscribe((ev: TerminalOutput) => {
      if (ev.type === 'output' && ev.data) {
        this.term?.write(ev.data);
      } else if (ev.type === 'error' && ev.message) {
        this.term?.writeln(`\r\n\x1b[31m${ev.message}\x1b[0m`);
      } else if (ev.type === 'ready') {
        if (ev.message && (ev.mode === 'waiting' || ev.mode === 'mirror')) {
          this.term?.writeln(`\r\n\x1b[90m${ev.message}\x1b[0m\r\n`);
        }
        if (!this.mirrorAgent) {
          this.applyCwd();
        }
        this.reflow();
      } else if (ev.type === 'mode' && ev.mode === 'mirror') {
        this.term?.writeln('\r\n\x1b[90mAgent shell connected — read-only mirror.\x1b[0m\r\n');
      }
    });

    this.resizeObserver = new ResizeObserver(() => this.reflow());
    this.resizeObserver.observe(this.host.nativeElement);

    const connectAgent = this.mirrorAgent && !!this.agentId;
    void this.connection.connect({
      agentId: connectAgent ? this.agentId : undefined,
      workspacePath: connectAgent ? (this.workspacePath || this.cwd) : undefined,
    }).then(() => {
      this.term?.onData((data) => {
        if (!this.connection?.mirrorMode()) {
          this.connection?.sendInput(data);
        }
      });
      requestAnimationFrame(() => this.focusTerminal());
    }).catch(() => {
      // Error surfaced via onOutput subscription.
    });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['cwd'] && !changes['cwd'].firstChange && !this.mirrorAgent) {
      this.cwdApplied = false;
      this.applyCwd();
    }
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.outputSub?.unsubscribe();
    this.term?.dispose();
    this.connection?.disconnect();
  }

  @HostListener('window:resize')
  onWindowResize(): void {
    this.reflow();
  }

  onHostPointerDown(event: MouseEvent): void {
    if ((event.target as HTMLElement).closest('.close-btn')) return;
    this.focusTerminal();
  }

  closePanel(): void {
    this.closed.emit();
  }

  private applyCwd(): void {
    if (!this.cwd || this.cwdApplied || !this.connection?.ready()) return;
    this.connection.setCwd(this.cwd);
    this.cwdApplied = true;
  }

  private focusTerminal(): void {
    this.term?.focus();
  }

  private reflow(): void {
    this.fitAddon?.fit();
    if (this.term && this.connection) {
      this.connection.resize(this.term.cols, this.term.rows);
    }
  }
}
