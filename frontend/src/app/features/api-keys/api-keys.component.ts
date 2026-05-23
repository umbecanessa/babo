import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiKeyService } from '../../core/services/api-key.service';
import { ApiKey } from '../../core/models/user.model';
import { ToastService } from '../../shared/toast/toast.service';

@Component({
  selector: 'app-api-keys',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './api-keys.component.html',
  styleUrl: './api-keys.component.scss',
})
export class ApiKeysComponent implements OnInit {
  keys = signal<ApiKey[]>([]);
  newKeyName = '';
  showCreateModal = signal(false);
  createdKey = signal<string | null>(null);
  copied = signal(false);

  constructor(private apiKeyService: ApiKeyService, private toast: ToastService) {}

  ngOnInit() {
    this.loadKeys();
  }

  loadKeys() {
    this.apiKeyService.getKeys().subscribe({
      next: (keys) => this.keys.set(keys),
      error: (err) => {
        console.error('Failed to load API keys:', err);
        this.toast.show('Failed to load API keys. Check backend connection.', 'error');
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
        this.toast.show('Failed to generate API key. Check backend connection.', 'error');
      },
    });
  }

  revokeKey(id: string) {
    this.apiKeyService.revokeKey(id).subscribe({
      next: () => this.loadKeys(),
      error: (err) => {
        console.error('Failed to revoke key:', err);
        this.toast.show('Failed to revoke key.', 'error');
      },
    });
  }

  deleteKey(id: string) {
    this.apiKeyService.deleteKey(id).subscribe({
      next: () => this.loadKeys(),
      error: (err) => {
        console.error('Failed to delete key:', err);
        this.toast.show('Failed to delete key.', 'error');
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
