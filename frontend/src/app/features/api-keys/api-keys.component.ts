import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { ApiKeyService } from '../../core/services/api-key.service';
import { ApiKey } from '../../core/models/user.model';
import { ToastService } from '../../shared/toast/toast.service';

@Component({
  selector: 'app-api-keys',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslateModule],
  templateUrl: './api-keys.component.html',
  styleUrl: './api-keys.component.scss',
})
export class ApiKeysComponent implements OnInit {
  readonly pythonSnippet = `from openai import OpenAI

client = OpenAI(
    base_url="https://nls-api.up.railway.app/v1",
    api_key="nlsk_your_key_here"
)

response = client.chat.completions.create(
    model="agent:your-agent-id",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)
for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")`;

  readonly curlSnippet = `curl https://nls-api.up.railway.app/v1/chat/completions \\
  -H "Authorization: Bearer nlsk_your_key_here" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"agent:your-agent-id","messages":[{"role":"user","content":"Hello!"}]}'`;

  keys = signal<ApiKey[]>([]);
  newKeyName = '';
  showCreateModal = signal(false);
  createdKey = signal<string | null>(null);
  copied = signal(false);

  constructor(
    private apiKeyService: ApiKeyService,
    private toast: ToastService,
    private translate: TranslateService,
  ) {}

  ngOnInit() {
    this.loadKeys();
  }

  loadKeys() {
    this.apiKeyService.getKeys().subscribe({
      next: (keys) => this.keys.set(keys),
      error: (err) => {
        console.error('Failed to load API keys:', err);
        this.toast.show(this.translate.instant('apiKeys.loadFailed'), 'error');
      },
    });
  }

  createKey() {
    if (!this.newKeyName.trim()) return;

    this.apiKeyService.createKey(this.newKeyName).subscribe({
      next: (key) => {
        this.createdKey.set(key.key || null);
        this.newKeyName = '';
        this.loadKeys();
      },
      error: (err) => {
        console.error('Failed to create API key:', err);
        this.toast.show(this.translate.instant('apiKeys.generateFailed'), 'error');
      },
    });
  }

  revokeKey(id: string) {
    this.apiKeyService.revokeKey(id).subscribe({
      next: () => this.loadKeys(),
      error: (err) => {
        console.error('Failed to revoke key:', err);
        this.toast.show(this.translate.instant('apiKeys.revokeFailed'), 'error');
      },
    });
  }

  deleteKey(id: string) {
    this.apiKeyService.deleteKey(id).subscribe({
      next: () => this.loadKeys(),
      error: (err) => {
        console.error('Failed to delete key:', err);
        this.toast.show(this.translate.instant('apiKeys.deleteFailed'), 'error');
      },
    });
  }

  copyKey() {
    const key = this.createdKey();
    if (key) {
      navigator.clipboard.writeText(key);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    }
  }

  closeModal() {
    this.createdKey.set(null);
    this.showCreateModal.set(false);
  }
}
