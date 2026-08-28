import {
  Component,
  Input,
  Output,
  EventEmitter,
  ElementRef,
  ViewChild,
  AfterViewInit,
  OnDestroy,
  OnChanges,
  SimpleChanges,
  NgZone,
  ChangeDetectorRef,
  NO_ERRORS_SCHEMA,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';

const GET_INTERACTIVE_ELEMENTS_JS = `
(function() {
  const selectors = 'a[href], button, input, textarea, select, [role="button"], [onclick], [tabindex]';
  const els = document.querySelectorAll(selectors);
  const results = [];
  const seen = new Set();
  for (const el of els) {
    if (el.offsetParent === null && el.tagName !== 'INPUT' && el.type !== 'hidden') continue;
    const tag = el.tagName.toLowerCase();
    const type = el.type || '';
    const text = (el.textContent || '').trim().substring(0, 80);
    const placeholder = el.placeholder || '';
    const name = el.name || '';
    const id = el.id || '';
    const href = el.href || '';
    const ariaLabel = el.getAttribute('aria-label') || '';
    const value = (tag === 'input' || tag === 'textarea') ? (el.value || '').substring(0, 40) : '';
    let sel = '';
    if (id) {
      sel = '#' + CSS.escape(id);
    } else if (name && (tag === 'input' || tag === 'textarea' || tag === 'select')) {
      sel = tag + '[name="' + name.replace(/"/g, '\\\\"') + '"]';
    } else {
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
        const idx = siblings.indexOf(el) + 1;
        const parentId = parent.id ? '#' + CSS.escape(parent.id) + ' > ' : '';
        sel = parentId + tag + ':nth-of-type(' + idx + ')';
      } else {
        sel = tag;
      }
    }
    const key = sel + '|' + text;
    if (seen.has(key)) continue;
    seen.add(key);
    const entry = { tag, selector: sel };
    if (type) entry.type = type;
    if (text) entry.text = text;
    if (placeholder) entry.placeholder = placeholder;
    if (ariaLabel) entry.ariaLabel = ariaLabel;
    if (href) entry.href = href;
    if (value) entry.value = value;
    results.push(entry);
    if (results.length >= 60) break;
  }
  return JSON.stringify(results);
})()
`;

const GET_TEXT_JS = `
(function() {
  return document.body.innerText.substring(0, 8000);
})()
`;

@Component({
  selector: 'app-agent-browser',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  schemas: [NO_ERRORS_SCHEMA],
  templateUrl: './agent-browser.component.html',
  styleUrl: './agent-browser.component.scss',
})
export class AgentBrowserComponent implements AfterViewInit, OnDestroy, OnChanges {
  @Input() command: any = null;

  @Output() commandResult = new EventEmitter<any>();
  @Output() collapseRequest = new EventEmitter<void>();

  @ViewChild('webviewRef') webviewRef!: ElementRef;

  pageTitle = '';
  pageUrl = '';
  isLoading = false;
  hasError = false;
  errorMessage = '';

  private _ready = false;
  private _pendingCommand: any = null;
  private _readyPollTimer: any = null;
  private _navPollTimer: any = null;

  constructor(private zone: NgZone, private cdr: ChangeDetectorRef) {}

  ngAfterViewInit(): void {
    this._waitForReady();
    this._bindWebviewEvents();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['command'] && this.command) {
      if (this._ready) {
        this._dispatch(this.command);
      } else {
        this._pendingCommand = this.command;
      }
    }
  }

  ngOnDestroy(): void {
    clearInterval(this._readyPollTimer);
    clearInterval(this._navPollTimer);
  }

  onCollapse(): void {
    this.collapseRequest.emit();
  }

  // ─── Wait for webview to be ready (polling, no events) ─────

  private _waitForReady(): void {
    const wv = this.webviewRef?.nativeElement;
    if (!wv) {
      console.error('[BROWSER] No webview ref in _waitForReady');
      return;
    }

    console.log('[BROWSER] Starting ready poll...');
    this._readyPollTimer = setInterval(() => {
      try {
        const loading = wv.isLoading?.();
        if (loading === false) {
          clearInterval(this._readyPollTimer);
          this._ready = true;
          console.log('[BROWSER] Webview READY. Pending command:', !!this._pendingCommand);

          if (this._pendingCommand) {
            const cmd = this._pendingCommand;
            this._pendingCommand = null;
            this._dispatch(cmd);
          }
        }
      } catch (e) {
        console.log('[BROWSER] Ready poll error (expected):', e);
      }
    }, 100);
  }

  // ─── Passive webview event tracking (CDP mode) ─────────────

  private _bindWebviewEvents(): void {
    const wv = this.webviewRef?.nativeElement;
    if (!wv) return;

    wv.addEventListener('did-navigate', (e: any) => {
      this.zone.run(() => {
        this.pageUrl = e.url || '';
        try { this.pageTitle = wv.getTitle?.() || ''; } catch { /* ok */ }
        this.isLoading = false;
        this.cdr.markForCheck();
      });
    });

    wv.addEventListener('did-navigate-in-page', (e: any) => {
      this.zone.run(() => {
        this.pageUrl = e.url || '';
        try { this.pageTitle = wv.getTitle?.() || ''; } catch { /* ok */ }
        this.cdr.markForCheck();
      });
    });

    wv.addEventListener('did-start-loading', () => {
      this.zone.run(() => {
        this.isLoading = true;
        this.cdr.markForCheck();
      });
    });

    wv.addEventListener('did-stop-loading', () => {
      this.zone.run(() => {
        this.isLoading = false;
        try {
          this.pageUrl = wv.getURL?.() || this.pageUrl;
          this.pageTitle = wv.getTitle?.() || this.pageTitle;
        } catch { /* ok */ }
        this.cdr.markForCheck();
      });
    });

    wv.addEventListener('page-title-updated', (e: any) => {
      this.zone.run(() => {
        this.pageTitle = e.title || '';
        this.cdr.markForCheck();
      });
    });
  }

  // ─── Command dispatcher ────────────────────────────────────

  private async _dispatch(cmd: any): Promise<void> {
    const action = cmd.action || 'navigate';
    const requestId = cmd.request_id || '';
    console.log(`[BROWSER] _dispatch action=${action} requestId=${requestId}`, cmd);

    try {
      switch (action) {
        case 'navigate':
          this._doNavigate(cmd.url || '', requestId);
          break;
        case 'click':
          await this._doClick(cmd.selector || '', requestId);
          break;
        case 'type':
          await this._doType(cmd.text || '', requestId);
          break;
        case 'fill':
          await this._doFill(cmd.selector || '', cmd.value || '', requestId);
          break;
        case 'scroll':
          await this._doScroll(cmd.direction || 'down', cmd.amount || 500, requestId);
          break;
        case 'screenshot':
          await this._doScreenshot(requestId);
          break;
        case 'screenshot_raw':
          await this._doScreenshotRaw(requestId);
          break;
        case 'get_text':
          await this._doGetText(requestId);
          break;
        case 'get_interactive_elements':
          await this._doGetInteractiveElements(requestId);
          break;
        default:
          this._emitResult(requestId, action, 'error', '', `Unknown action: ${action}`);
      }
    } catch (err: any) {
      this._emitResult(requestId, action, 'error', '', err?.message || 'Unknown error');
    }
  }

  // ─── Navigate with polling ─────────────────────────────────

  private _doNavigate(url: string, requestId: string): void {
    if (!url) {
      this._emitResult(requestId, 'navigate', 'error', '', 'URL is required');
      return;
    }

    const wv = this.webviewRef?.nativeElement;
    if (!wv) {
      this._emitResult(requestId, 'navigate', 'error', '', 'Webview not available');
      return;
    }

    this.isLoading = true;
    this.hasError = false;
    this.errorMessage = '';
    this.pageUrl = url;
    this.cdr.markForCheck();

    // Clear any previous nav poll
    clearInterval(this._navPollTimer);

    console.log(`[BROWSER] loadURL(${url}) requestId=${requestId}`);
    wv.loadURL(url);

    let elapsed = 0;
    this._navPollTimer = setInterval(() => {
      elapsed += 500;
      try {
        const stillLoading = wv.isLoading?.();
        const currentUrl = wv.getURL?.() || '';

        if (elapsed % 2000 === 0) {
          console.log(`[BROWSER] Nav poll ${elapsed}ms: loading=${stillLoading} url=${currentUrl.substring(0, 60)}`);
        }

        if (stillLoading === false && currentUrl && currentUrl !== 'about:blank') {
          clearInterval(this._navPollTimer);
          this._navPollTimer = null;

          const title = wv.getTitle?.() || currentUrl;
          console.log(`[BROWSER] Nav DONE: url=${currentUrl.substring(0, 80)} title=${title}`);

          this.zone.run(async () => {
            this.isLoading = false;
            this.pageUrl = currentUrl;
            this.pageTitle = title;
            this.cdr.markForCheck();

            const challenge = await this._detectChallenge();
            let resultText = `Navigated to: ${this.pageUrl}\nPage title: ${this.pageTitle}`;
            if (challenge) {
              resultText += `\n\n⚠️ BLOCKED: ${challenge} detected on this page. `
                + `You cannot interact with this page directly. `
                + `Use ask_user() to request the human's help to solve the challenge.`;
              console.warn(`[BROWSER] Challenge detected: ${challenge}`);
            }

            console.log(`[BROWSER] Emitting navigate result requestId=${requestId}`);
            this._emitResult(requestId, 'navigate', challenge ? 'challenge' : 'ok', resultText);
          });
        }

        if (elapsed >= 55000) {
          clearInterval(this._navPollTimer);
          this._navPollTimer = null;
          console.error(`[BROWSER] Nav TIMEOUT after ${elapsed}ms`);
          this.zone.run(() => {
            this.isLoading = false;
            this.hasError = true;
            this.errorMessage = 'Page load timed out';
            this.cdr.markForCheck();
            this._emitResult(requestId, 'navigate', 'error', '', 'Page load timed out');
          });
        }
      } catch (e) {
        console.log('[BROWSER] Nav poll error:', e);
      }
    }, 500);
  }

  // ─── Other actions (unchanged) ─────────────────────────────

  private async _doClick(selector: string, requestId: string): Promise<void> {
    if (!selector) {
      this._emitResult(requestId, 'click', 'error', '', 'selector is required');
      return;
    }
    const wv = this.webviewRef?.nativeElement;
    if (!wv) {
      this._emitResult(requestId, 'click', 'error', '', 'Webview not available');
      return;
    }

    // Get element position and info via JS, then click via native mouse
    // events so the click is "trusted" (passes React/CSP checks).
    const js = `
      (function() {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return JSON.stringify({ error: 'Element not found: ' + ${JSON.stringify(selector)} });
        el.scrollIntoView({ block: 'center', behavior: 'instant' });
        const rect = el.getBoundingClientRect();
        const x = Math.round(rect.left + rect.width / 2);
        const y = Math.round(rect.top + rect.height / 2);
        const tag = el.tagName.toLowerCase();
        const text = (el.textContent || '').trim().substring(0, 60);
        return JSON.stringify({ ok: true, tag, text, x, y });
      })()
    `;
    const raw = await this._exec(js);
    const res = this._parseJson(raw);
    if (res.error) {
      this._emitResult(requestId, 'click', 'error', '', res.error);
      return;
    }

    // Send trusted mouse events at the element's center
    const x = res.x || 0;
    const y = res.y || 0;
    wv.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
    await this._sleep(50);
    wv.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });

    // Wait for navigation/UI updates triggered by the click
    await this._sleep(500);

    // Update page title/url if navigation happened
    try {
      const newUrl = wv.getURL?.() || this.pageUrl;
      const newTitle = wv.getTitle?.() || this.pageTitle;
      if (newUrl !== this.pageUrl) {
        this.pageUrl = newUrl;
        this.pageTitle = newTitle;
        this.cdr.markForCheck();
      }
    } catch { /* ignore */ }

    // Check if the click led to a challenge page
    const challenge = await this._detectChallenge();
    let resultMsg = `Clicked <${res.tag}>${res.text ? ' "' + res.text + '"' : ''} at (${x}, ${y})`;
    if (challenge) {
      resultMsg += `\n⚠️ After clicking, a ${challenge} appeared. Use ask_user() to request human help.`;
    }
    this._emitResult(requestId, 'click', challenge ? 'challenge' : 'ok', resultMsg);
  }

  private static readonly SPECIAL_KEYS: Record<string, string> = {
    '{ENTER}': 'Return',
    '{TAB}': 'Tab',
    '{ESCAPE}': 'Escape',
    '{BACKSPACE}': 'Backspace',
    '{DELETE}': 'Delete',
    '{ARROWUP}': 'Up',
    '{ARROWDOWN}': 'Down',
    '{ARROWLEFT}': 'Left',
    '{ARROWRIGHT}': 'Right',
    '{HOME}': 'Home',
    '{END}': 'End',
    '{PAGEUP}': 'PageUp',
    '{PAGEDOWN}': 'PageDown',
    '{SPACE}': 'Space',
  };

  private async _doType(text: string, requestId: string): Promise<void> {
    if (!text) {
      this._emitResult(requestId, 'type', 'error', '', 'text is required');
      return;
    }
    const wv = this.webviewRef?.nativeElement;
    if (!wv) {
      this._emitResult(requestId, 'type', 'error', '', 'Webview not available');
      return;
    }

    const tokens = this._tokenizeTypeText(text);
    for (const token of tokens) {
      if (token.special) {
        wv.sendInputEvent({ type: 'keyDown', keyCode: token.key });
        wv.sendInputEvent({ type: 'keyUp', keyCode: token.key });
      } else {
        for (const char of token.key) {
          wv.sendInputEvent({ type: 'keyDown', keyCode: char });
          wv.sendInputEvent({ type: 'char', keyCode: char });
          wv.sendInputEvent({ type: 'keyUp', keyCode: char });
        }
      }
    }
    this._emitResult(requestId, 'type', 'ok', `Typed ${text.length} characters`);
  }

  private _tokenizeTypeText(text: string): Array<{ key: string; special: boolean }> {
    const tokens: Array<{ key: string; special: boolean }> = [];
    let i = 0;
    while (i < text.length) {
      if (text[i] === '\n' || text[i] === '\r') {
        tokens.push({ key: 'Return', special: true });
        if (text[i] === '\r' && text[i + 1] === '\n') i++;
        i++;
        continue;
      }
      if (text[i] === '{') {
        const end = text.indexOf('}', i);
        if (end !== -1) {
          const tag = text.substring(i, end + 1).toUpperCase();
          const mapped = AgentBrowserComponent.SPECIAL_KEYS[tag];
          if (mapped) {
            tokens.push({ key: mapped, special: true });
            i = end + 1;
            continue;
          }
        }
      }
      tokens.push({ key: text[i], special: false });
      i++;
    }
    return tokens;
  }

  private async _doFill(selector: string, value: string, requestId: string): Promise<void> {
    if (!selector) {
      this._emitResult(requestId, 'fill', 'error', '', 'selector is required');
      return;
    }
    const wv = this.webviewRef?.nativeElement;
    if (!wv) {
      this._emitResult(requestId, 'fill', 'error', '', 'Webview not available');
      return;
    }

    // Step 1: Focus the element and click it (native trusted click)
    const js = `
      (function() {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return JSON.stringify({ error: 'Element not found: ' + ${JSON.stringify(selector)} });
        el.scrollIntoView({ block: 'center', behavior: 'instant' });
        const rect = el.getBoundingClientRect();
        const x = Math.round(rect.left + rect.width / 2);
        const y = Math.round(rect.top + rect.height / 2);
        return JSON.stringify({ ok: true, tag: el.tagName.toLowerCase(), x, y });
      })()
    `;
    const raw = await this._exec(js);
    const res = this._parseJson(raw);
    if (res.error) {
      this._emitResult(requestId, 'fill', 'error', '', res.error);
      return;
    }

    // Click to focus the field with trusted mouse events
    const x = res.x || 0;
    const y = res.y || 0;
    wv.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
    await this._sleep(30);
    wv.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
    await this._sleep(100);

    // Step 2: Select-all and delete existing content
    wv.sendInputEvent({ type: 'keyDown', keyCode: 'a', modifiers: ['control'] });
    wv.sendInputEvent({ type: 'keyUp', keyCode: 'a', modifiers: ['control'] });
    await this._sleep(30);
    wv.sendInputEvent({ type: 'keyDown', keyCode: 'Backspace' });
    wv.sendInputEvent({ type: 'keyUp', keyCode: 'Backspace' });
    await this._sleep(30);

    // Step 3: Type each character natively so React/Angular detect input
    for (const char of value) {
      wv.sendInputEvent({ type: 'keyDown', keyCode: char });
      wv.sendInputEvent({ type: 'char', keyCode: char });
      wv.sendInputEvent({ type: 'keyUp', keyCode: char });
    }

    // Step 4: Also set via JS as backup (some frameworks need both)
    await this._exec(`
      (function() {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (el) {
          const nativeSet = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
          )?.set || Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value'
          )?.set;
          if (nativeSet) nativeSet.call(el, ${JSON.stringify(value)});
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
      })()
    `);

    const preview = value.length > 40 ? value.substring(0, 40) + '...' : value;
    this._emitResult(requestId, 'fill', 'ok', `Filled <${res.tag}> with "${preview}"`);
  }

  private async _doScroll(direction: string, amount: number, requestId: string): Promise<void> {
    const px = direction === 'up' ? -Math.abs(amount) : Math.abs(amount);
    const js = `window.scrollBy(0, ${px}); JSON.stringify({ scrollY: window.scrollY })`;
    const raw = await this._exec(js);
    const res = this._parseJson(raw);
    this._emitResult(requestId, 'scroll', 'ok',
      `Scrolled ${direction} ${Math.abs(amount)}px (scrollY: ${res.scrollY || '?'})`);
  }

  private async _doScreenshot(requestId: string): Promise<void> {
    const js = `
      (function() {
        const title = document.title;
        const url = location.href;
        const text = document.body.innerText.substring(0, 4000);
        return JSON.stringify({ title, url, text });
      })()
    `;
    const raw = await this._exec(js);
    const res = this._parseJson(raw);
    this._emitResult(requestId, 'screenshot', 'ok',
      `Page: ${res.title || '?'} (${res.url || '?'})\n\n${res.text || '(empty)'}`);
  }

  private async _doScreenshotRaw(requestId: string): Promise<void> {
    const wv = this.webviewRef?.nativeElement;
    if (!wv) {
      this._emitResult(requestId, 'screenshot_raw', 'error', '', 'Webview not available');
      return;
    }
    try {
      const nativeImage = await wv.capturePage();
      const b64 = nativeImage.toJPEG(70).toString('base64');
      // Emit result with the image in a special field; chat.component forwards it
      this.zone.run(() => {
        this.commandResult.emit({
          requestId,
          action: 'screenshot_raw',
          status: 'ok',
          result: 'screenshot_raw captured',
          image_base64: `data:image/jpeg;base64,${b64}`,
          title: this.pageTitle,
          url: this.pageUrl,
        });
      });
    } catch (err: any) {
      this._emitResult(requestId, 'screenshot_raw', 'error', '', err?.message || 'capturePage failed');
    }
  }

  private async _doGetText(requestId: string): Promise<void> {
    const raw = await this._exec(GET_TEXT_JS);
    this._emitResult(requestId, 'get_text', 'ok', raw || '(empty page)');
  }

  private async _doGetInteractiveElements(requestId: string): Promise<void> {
    const raw = await this._exec(GET_INTERACTIVE_ELEMENTS_JS);

    // Check if executeJavaScript itself failed (CSP or page not ready)
    if (!raw || raw === '""' || raw === '') {
      const challenge = await this._detectChallenge();
      if (challenge) {
        this._emitResult(requestId, 'get_interactive_elements', 'error', '',
          `Cannot read page elements: ${challenge} detected. Use ask_user() to request human help.`);
      } else {
        this._emitResult(requestId, 'get_interactive_elements', 'ok', 'No interactive elements found (page may still be loading).');
      }
      return;
    }

    let elements: any[];
    try {
      const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
      elements = Array.isArray(parsed) ? parsed : [];
    } catch {
      // If JSON parse fails, the page likely blocked script execution
      const challenge = await this._detectChallenge();
      if (challenge) {
        this._emitResult(requestId, 'get_interactive_elements', 'error', '',
          `Script blocked by page (${challenge}). Use ask_user() to request human help.`);
      } else {
        this._emitResult(requestId, 'get_interactive_elements', 'error', '', 'Failed to parse elements from page.');
      }
      return;
    }

    if (!elements.length) {
      this._emitResult(requestId, 'get_interactive_elements', 'ok', 'No interactive elements found.');
      return;
    }
    const lines = elements.map((el: any, i: number) => {
      let desc = `[${i}] <${el.tag}`;
      if (el.type) desc += ` type="${el.type}"`;
      desc += `> selector="${el.selector}"`;
      if (el.text) desc += ` text="${el.text}"`;
      if (el.placeholder) desc += ` placeholder="${el.placeholder}"`;
      if (el.ariaLabel) desc += ` aria-label="${el.ariaLabel}"`;
      if (el.href) desc += ` href="${el.href}"`;
      if (el.value) desc += ` value="${el.value}"`;
      return desc;
    });
    const header = `Found ${elements.length} interactive elements on ${this.pageUrl}:\n`;
    this._emitResult(requestId, 'get_interactive_elements', 'ok', header + lines.join('\n'));
  }

  // ─── Helpers ───────────────────────────────────────────────

  private _sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  private _exec(js: string): Promise<string> {
    const wv = this.webviewRef?.nativeElement;
    if (!wv) return Promise.resolve('');
    return wv.executeJavaScript(js).catch(
      (e: any) => JSON.stringify({ error: e?.message || 'JS execution failed' })
    );
  }

  private _parseJson(raw: any): any {
    if (typeof raw === 'object' && raw !== null) return raw;
    try { return JSON.parse(raw); } catch { return { raw }; }
  }

  /**
   * After navigation completes, check if the page is a CAPTCHA,
   * bot-challenge, or verification gate.  Returns a hint string
   * if detected, otherwise empty string.
   */
  private async _detectChallenge(): Promise<string> {
    const raw = await this._exec(`
      (function() {
        const title = (document.title || '').toLowerCase();
        const body = (document.body?.innerText || '').substring(0, 2000).toLowerCase();
        const url = location.href.toLowerCase();

        if (title.includes('captcha') || body.includes('captcha'))
          return 'CAPTCHA';
        if (title.includes('verify you are human') || body.includes('verify you are human'))
          return 'Human verification';
        if (title.includes('are you a robot') || body.includes('are you a robot'))
          return 'Robot check';
        if (url.includes('recaptcha') || body.includes('recaptcha'))
          return 'reCAPTCHA';
        if (title.includes('just a moment') || body.includes('checking your browser'))
          return 'Cloudflare challenge';
        if (url.includes('challenge') && body.includes('verify'))
          return 'Security challenge';
        if (body.includes('unusual traffic') || body.includes('automated requests'))
          return 'Bot detection';
        if (title.includes('denied') && body.includes('access'))
          return 'Access denied';
        if (body.includes('enable javascript') && body.length < 500)
          return 'JS required gate';
        return '';
      })()
    `);
    return (typeof raw === 'string') ? raw : '';
  }

  private _emitResult(requestId: string, action: string, status: string, result = '', error = ''): void {
    console.log(`[BROWSER] _emitResult: action=${action} status=${status} reqId=${requestId} error=${error || 'none'}`);
    this.zone.run(() => {
      this.commandResult.emit({
        requestId,
        action,
        status,
        result,
        error,
        title: this.pageTitle,
        url: this.pageUrl,
      });
    });
  }
}
