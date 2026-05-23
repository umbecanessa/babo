/**
 * NLS Desktop App -- Permission Manager
 *
 * Apple-style permission system for desktop capabilities.
 * Tools and IPC handlers check permissions before executing.
 * Users see clean dialogs when new permissions are requested.
 *
 * Permission categories:
 *   - filesystem.read    (scoped to directories)
 *   - filesystem.write   (scoped to directories)
 *   - shell.execute      (sandboxed or full)
 *   - clipboard.read
 *   - clipboard.write
 *   - notification
 *   - network.outbound   (scoped to domains)
 *   - camera
 *   - microphone
 *   - keychain
 */

import { app, dialog, BrowserWindow } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PermissionGrant {
  permission: string;
  granted: boolean;
  scope?: string; // e.g., directory path or domain
  grantedAt: string; // ISO timestamp
}

interface PermissionProfile {
  name: string;
  description: string;
  grants: Record<string, boolean>;
}

// ---------------------------------------------------------------------------
// Built-in profiles
// ---------------------------------------------------------------------------

const PROFILES: Record<string, PermissionProfile> = {
  research: {
    name: 'Research Assistant',
    description: 'Web browsing and read-only file access',
    grants: {
      'filesystem.read': true,
      'filesystem.write': false,
      'shell.execute': false,
      'clipboard.read': true,
      'clipboard.write': true,
      'notification': true,
      'network.outbound': true,
    },
  },
  developer: {
    name: 'Developer',
    description: 'Full development capabilities',
    grants: {
      'filesystem.read': true,
      'filesystem.write': true,
      'shell.execute': true,
      'clipboard.read': true,
      'clipboard.write': true,
      'notification': true,
      'network.outbound': true,
    },
  },
  private: {
    name: 'Private',
    description: 'No network access, local only',
    grants: {
      'filesystem.read': true,
      'filesystem.write': true,
      'shell.execute': false,
      'clipboard.read': true,
      'clipboard.write': true,
      'notification': true,
      'network.outbound': false,
    },
  },
};

// ---------------------------------------------------------------------------
// Permission Manager
// ---------------------------------------------------------------------------

export class PermissionManager {
  private grants: Map<string, PermissionGrant> = new Map();
  private configPath: string;

  constructor() {
    this.configPath = path.join(
      app.getPath('userData'),
      'permissions.json',
    );
    this.load();
  }

  /**
   * Check if a permission is granted. Throws if denied.
   * Called by IPC handlers before executing privileged operations.
   */
  async require(permission: string, scope?: string): Promise<void> {
    const key = scope ? `${permission}:${scope}` : permission;
    const baseKey = permission;

    // Check specific scope first, then base permission
    const grant = this.grants.get(key) || this.grants.get(baseKey);

    if (grant && grant.granted) {
      return; // Already granted
    }

    if (grant && !grant.granted) {
      throw new Error(
        `Permission denied: ${permission}${scope ? ` (${scope})` : ''}`,
      );
    }

    // Not yet decided -- prompt the user
    const allowed = await this.promptUser(permission, scope);

    // Store the decision
    this.grants.set(key, {
      permission,
      granted: allowed,
      scope,
      grantedAt: new Date().toISOString(),
    });
    this.save();

    if (!allowed) {
      throw new Error(
        `Permission denied: ${permission}${scope ? ` (${scope})` : ''}`,
      );
    }
  }

  /**
   * Request a permission (from the renderer side).
   */
  async request(permission: string, reason: string): Promise<boolean> {
    const grant = this.grants.get(permission);
    if (grant) {
      return grant.granted;
    }

    const allowed = await this.promptUser(permission, undefined, reason);

    this.grants.set(permission, {
      permission,
      granted: allowed,
      grantedAt: new Date().toISOString(),
    });
    this.save();

    return allowed;
  }

  /**
   * Get all permission states.
   */
  getAll(): Record<string, boolean> {
    const result: Record<string, boolean> = {};
    for (const [key, grant] of this.grants) {
      result[key] = grant.granted;
    }
    return result;
  }

  /**
   * Apply a permission profile.
   */
  applyProfile(profileName: string): void {
    const profile = PROFILES[profileName];
    if (!profile) return;

    for (const [permission, granted] of Object.entries(profile.grants)) {
      this.grants.set(permission, {
        permission,
        granted,
        grantedAt: new Date().toISOString(),
      });
    }
    this.save();
  }

  /**
   * Get available profiles.
   */
  getProfiles(): PermissionProfile[] {
    return Object.values(PROFILES);
  }

  /**
   * Reset all permissions.
   */
  reset(): void {
    this.grants.clear();
    this.save();
  }

  // ─── User prompt ────────────────────────────────────────────────────

  private async promptUser(
    permission: string,
    scope?: string,
    reason?: string,
  ): Promise<boolean> {
    const win = BrowserWindow.getFocusedWindow();

    const permissionLabels: Record<string, string> = {
      'filesystem.read': 'read files',
      'filesystem.write': 'write files',
      'shell.execute': 'run shell commands',
      'clipboard.read': 'read the clipboard',
      'clipboard.write': 'write to the clipboard',
      'notification': 'show notifications',
      'network.outbound': 'access the network',
      'camera': 'use the camera',
      'microphone': 'use the microphone',
      'keychain': 'access stored credentials',
    };

    const label = permissionLabels[permission] || permission;
    const scopeText = scope ? `\n\nScope: ${scope}` : '';
    const reasonText = reason ? `\n\nReason: ${reason}` : '';

    const result = await dialog.showMessageBox(win!, {
      type: 'question',
      title: 'Babo Permission Request',
      message: `Babo wants to ${label}`,
      detail: `Your agent is requesting permission to ${label}.${scopeText}${reasonText}\n\nYou can change this later in Settings > Permissions.`,
      buttons: ['Allow', 'Deny'],
      defaultId: 0,
      cancelId: 1,
      icon: undefined, // TODO: NLS icon
    });

    return result.response === 0;
  }

  // ─── Persistence ────────────────────────────────────────────────────

  private load(): void {
    try {
      if (fs.existsSync(this.configPath)) {
        const data = JSON.parse(
          fs.readFileSync(this.configPath, 'utf-8'),
        );
        for (const grant of data.grants || []) {
          this.grants.set(
            grant.scope
              ? `${grant.permission}:${grant.scope}`
              : grant.permission,
            grant,
          );
        }
      }
    } catch {
      // Fresh start if config is corrupted
      this.grants.clear();
    }
  }

  private save(): void {
    try {
      const dir = path.dirname(this.configPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(
        this.configPath,
        JSON.stringify(
          { grants: Array.from(this.grants.values()) },
          null,
          2,
        ),
        'utf-8',
      );
    } catch {
      // Silently fail -- permissions will be re-prompted next time
    }
  }
}
