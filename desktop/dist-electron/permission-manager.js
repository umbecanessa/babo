"use strict";
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
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.PermissionManager = void 0;
const electron_1 = require("electron");
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
// ---------------------------------------------------------------------------
// Built-in profiles
// ---------------------------------------------------------------------------
const PROFILES = {
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
class PermissionManager {
    grants = new Map();
    configPath;
    constructor() {
        this.configPath = path.join(electron_1.app.getPath('userData'), 'permissions.json');
        this.load();
    }
    /**
     * Check if a permission is granted. Throws if denied.
     * Called by IPC handlers before executing privileged operations.
     *
     * Scoped filesystem grants cover the granted directory and all paths beneath it,
     * so expanding nested folders does not re-prompt.
     */
    async require(permission, scope) {
        const access = this.resolveAccess(permission, scope);
        if (access === 'granted') {
            return;
        }
        if (access === 'denied') {
            throw new Error(`Permission denied: ${permission}${scope ? ` (${scope})` : ''}`);
        }
        // Desktop workspace: auto-grant local filesystem access (no interrupting dialogs).
        if (permission === 'filesystem.read' || permission === 'filesystem.write') {
            const key = this.grantKey(permission, scope);
            this.grants.set(key, {
                permission,
                granted: true,
                scope,
                grantedAt: new Date().toISOString(),
            });
            this.save();
            return;
        }
        const allowed = await this.promptUser(permission, scope);
        const key = this.grantKey(permission, scope);
        this.grants.set(key, {
            permission,
            granted: allowed,
            scope,
            grantedAt: new Date().toISOString(),
        });
        this.save();
        if (!allowed) {
            throw new Error(`Permission denied: ${permission}${scope ? ` (${scope})` : ''}`);
        }
    }
    /**
     * Request a permission (from the renderer side).
     */
    async request(permission, reason) {
        const grant = this.grants.get(permission);
        if (grant) {
            return grant.granted;
        }
        if (permission === 'filesystem.read' || permission === 'filesystem.write') {
            this.grants.set(permission, {
                permission,
                granted: true,
                grantedAt: new Date().toISOString(),
            });
            this.save();
            return true;
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
     * Collapse agent workspace paths to a single scope so the file explorer
     * only prompts once per workspace instead of per folder.
     */
    filesystemScope(filePath) {
        const normalized = path.normalize(filePath);
        const agentsRoot = normalized.match(/^(.+[\\/]agents[\\/][^\\/]+[\\/]workspace)(?:[\\/]|$)/i);
        if (agentsRoot) {
            return agentsRoot[1];
        }
        return filePath;
    }
    /**
     * Get all permission states.
     */
    getAll() {
        const result = {};
        for (const [key, grant] of this.grants) {
            result[key] = grant.granted;
        }
        return result;
    }
    /**
     * Apply a permission profile.
     */
    applyProfile(profileName) {
        const profile = PROFILES[profileName];
        if (!profile)
            return;
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
    getProfiles() {
        return Object.entries(PROFILES).map(([id, profile]) => ({ id, ...profile }));
    }
    /**
     * Reset all permissions.
     */
    reset() {
        this.grants.clear();
        this.save();
    }
    // ─── User prompt ────────────────────────────────────────────────────
    /**
     * Resolve whether a permission is granted, denied, or not yet decided.
     * Directory scopes inherit to all nested paths.
     */
    resolveAccess(permission, scope) {
        const base = this.grants.get(permission);
        if (base?.granted === true)
            return 'granted';
        if (base?.granted === false)
            return 'denied';
        if (!scope) {
            return base ? (base.granted ? 'granted' : 'denied') : 'unknown';
        }
        const target = this.normalizePath(scope);
        // Exact scope decision (allow or deny) for this path
        for (const grant of this.grants.values()) {
            if (grant.permission !== permission || !grant.scope)
                continue;
            if (this.normalizePath(grant.scope) !== target)
                continue;
            return grant.granted ? 'granted' : 'denied';
        }
        // Inherit read/write from any granted parent directory
        for (const grant of this.grants.values()) {
            if (grant.permission !== permission || !grant.scope || !grant.granted) {
                continue;
            }
            const grantedDir = this.normalizePath(grant.scope);
            if (target === grantedDir || target.startsWith(`${grantedDir}/`)) {
                return 'granted';
            }
        }
        return 'unknown';
    }
    grantKey(permission, scope) {
        return scope ? `${permission}:${scope}` : permission;
    }
    /** Normalize paths for stable comparisons (Windows-safe, case-insensitive). */
    normalizePath(filePath) {
        return path
            .normalize(filePath)
            .replace(/\\/g, '/')
            .replace(/\/+$/, '')
            .toLowerCase();
    }
    async promptUser(permission, scope, reason) {
        const win = electron_1.BrowserWindow.getFocusedWindow();
        const permissionLabels = {
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
        const result = await electron_1.dialog.showMessageBox(win, {
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
    load() {
        try {
            if (fs.existsSync(this.configPath)) {
                const data = JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
                for (const grant of data.grants || []) {
                    this.grants.set(grant.scope
                        ? `${grant.permission}:${grant.scope}`
                        : grant.permission, grant);
                }
            }
        }
        catch {
            // Fresh start if config is corrupted
            this.grants.clear();
        }
    }
    save() {
        try {
            const dir = path.dirname(this.configPath);
            if (!fs.existsSync(dir)) {
                fs.mkdirSync(dir, { recursive: true });
            }
            fs.writeFileSync(this.configPath, JSON.stringify({ grants: Array.from(this.grants.values()) }, null, 2), 'utf-8');
        }
        catch {
            // Silently fail -- permissions will be re-prompted next time
        }
    }
}
exports.PermissionManager = PermissionManager;
//# sourceMappingURL=permission-manager.js.map