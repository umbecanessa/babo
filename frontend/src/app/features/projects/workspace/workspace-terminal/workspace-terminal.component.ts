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
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import { Subscription } from 'rxjs';
import { TerminalService, TerminalOutput } from '../../../../core/services/terminal.service';

@Component({
  selector: 'app-workspace-terminal',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './workspace-terminal.component.html',
  styleUrl: './workspace-terminal.component.scss',
})
export class WorkspaceTerminalComponent implements AfterViewInit, OnDestroy, OnChanges {
  @Input() cwd = '';
  @Output() closed = new EventEmitter<void>();
  @ViewChild('host') host!: ElementRef<HTMLDivElement>;

  private term?: Terminal;
  private fitAddon?: FitAddon;
  private outputSub?: Subscription;
  private resizeObserver?: ResizeObserver;
  private cwdApplied = false;

  constructor(private terminal: TerminalService) {}

  ngAfterViewInit(): void {
    this.term = new Terminal({
      cursorBlink: true,
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

    this.outputSub = this.terminal.onOutput().subscribe((ev: TerminalOutput) => {
      if (ev.type === 'output' && ev.data) {
        this.term?.write(ev.data);
      } else if (ev.type === 'error' && ev.message) {
        this.term?.writeln(`\r\n\x1b[31m${ev.message}\x1b[0m`);
      } else if (ev.type === 'ready') {
        this.applyCwd();
      }
    });

    this.term.onData((data) => this.terminal.sendInput(data));

    this.resizeObserver = new ResizeObserver(() => this.reflow());
    this.resizeObserver.observe(this.host.nativeElement);

    void this.terminal.connect().then(() => {
      this.applyCwd();
      this.focusTerminal();
    });

    requestAnimationFrame(() => this.reflow());
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['cwd'] && !changes['cwd'].firstChange) {
      this.cwdApplied = false;
      this.applyCwd();
    }
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.outputSub?.unsubscribe();
    this.term?.dispose();
    this.terminal.disconnect();
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
    if (!this.cwd || this.cwdApplied || !this.terminal.ready()) return;
    this.terminal.setCwd(this.cwd);
    this.cwdApplied = true;
  }

  private focusTerminal(): void {
    this.term?.focus();
  }

  private reflow(): void {
    this.fitAddon?.fit();
    if (this.term) {
      this.terminal.resize(this.term.cols, this.term.rows);
    }
  }
}
