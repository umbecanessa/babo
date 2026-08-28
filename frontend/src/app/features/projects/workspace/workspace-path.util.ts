import { normalizeWorkbenchFilePath } from '../../../core/services/activity-format.util';

/** Normalize workspace paths for stable comparisons. */
export function normalizeWorkspacePath(path: string): string {
  return (path || '').replace(/\\/g, '/').replace(/\/+$/, '');
}

/** Case-insensitive workspace path equality (Windows-safe). */
export function workspacePathsEqual(a: string, b: string): boolean {
  return (
    normalizeWorkspacePath(a).toLowerCase() === normalizeWorkspacePath(b).toLowerCase()
  );
}

/** True when `path` is the same entry as `parent` or nested under it. */
export function isPathUnderWorkspace(path: string, parent: string): boolean {
  const p = normalizeWorkspacePath(path).toLowerCase();
  const root = normalizeWorkspacePath(parent).toLowerCase();
  return p === root || p.startsWith(`${root}/`);
}

/** Re-root a path when a workspace folder or file was renamed. */
export function migrateWorkspacePath(
  oldRoot: string,
  newRoot: string,
  filePath: string,
): string {
  const normOld = normalizeWorkspacePath(oldRoot);
  const normNew = normalizeWorkspacePath(newRoot);
  const normFile = normalizeWorkspacePath(filePath);
  if (!normFile.toLowerCase().startsWith(normOld.toLowerCase())) {
    return filePath;
  }
  const suffix = normFile.slice(normOld.length);
  const sep = filePath.includes('\\') ? '\\' : '/';
  return normNew.replace(/\//g, sep) + suffix.replace(/\//g, sep);
}

/** Reject path traversal and path separators in a single workspace entry name. */
export function sanitizeWorkspaceEntryName(name: string): string {
  const trimmed = (name || '').trim();
  if (!trimmed) return '';
  if (/[/\\]/.test(trimmed) || trimmed.includes('..')) return '';
  return trimmed.replace(/[\0<>:"|?*]/g, '');
}

/** Resolve the on-disk workspace root for an agent (desktop shell). */
export function resolveAgentWorkspacePath(agentId: string): string {
  if (!agentId) return '';

  const nls = (window as { nls?: { getDataPath?: () => string } }).nls;
  const dataPath = nls?.getDataPath?.();
  if (!dataPath) return '';

  return `${dataPath}/agents/${agentId}/workspace`.replace(/\\/g, '/');
}

export function isInvalidWorkspacePathToken(filePath: string): boolean {
  const p = (filePath || '').trim().replace(/\\/g, '/').replace(/\/+$/, '');
  return !p || p === '/' || p === '.' || p === '..';
}

export function isAbsoluteFilesystemPath(filePath: string): boolean {
  const p = (filePath || '').trim();
  if (!p || isInvalidWorkspacePathToken(p)) return false;
  return /^[A-Za-z]:[\\/]/.test(p) || p.startsWith('\\\\') || (p.startsWith('/') && !p.startsWith('//'));
}

/** Normalize a chip/query path before resolving under the workspace. */
export function sanitizeWorkspaceChipPath(filePath: string): string {
  const p = (filePath || '').trim().replace(/\\/g, '/');
  if (isInvalidWorkspacePathToken(p)) return '';
  return p.replace(/\/+$/, '');
}

/**
 * Turn explorer absolute paths into workspace-relative form when possible.
 */
export function toWorkspaceRelativePath(
  filePath: string,
  workspaceRoot?: string,
): string {
  const norm = sanitizeWorkspaceChipPath(filePath);
  if (!norm) return '';
  if (!isAbsoluteFilesystemPath(norm)) {
    return norm.replace(/^\/+/, '');
  }

  const abs = norm.replace(/\\/g, '/');
  const root = (workspaceRoot || '').trim().replace(/\\/g, '/').replace(/\/+$/, '');
  if (root) {
    const absLower = abs.toLowerCase();
    const rootLower = root.toLowerCase();
    if (absLower === rootLower) return '';
    if (absLower.startsWith(`${rootLower}/`)) {
      return abs.slice(root.length).replace(/^\/+/, '');
    }
  }

  const extracted = normalizeWorkbenchFilePath(abs);
  if (extracted && !isAbsoluteFilesystemPath(extracted)) {
    return extracted;
  }
  return abs;
}

/** Prefix bare filenames with the plan project folder when needed. */
export function enrichWorkspaceRelativePath(
  filePath: string,
  projectDir?: string,
): string {
  const rel = sanitizeWorkspaceChipPath(filePath).replace(/^\/+/, '');
  if (!rel || isAbsoluteFilesystemPath(rel)) return rel;
  const pd = (projectDir || '').trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  if (!pd) return rel;
  if (rel === pd || rel.startsWith(`${pd}/`)) return rel;
  return `${pd}/${rel}`;
}

/** Ordered absolute paths to try when opening a workspace-relative file. */
export function buildWorkspaceFilePathCandidates(
  workspaceRoot: string,
  filePath: string,
  projectDir?: string,
): string[] {
  const cleaned = sanitizeWorkspaceChipPath(filePath);
  if (!cleaned) return [];

  const root = (workspaceRoot || '').trim().replace(/\\/g, '/').replace(/\/+$/, '');

  // File explorer passes absolute paths — use as-is when already under workspace root.
  if (isAbsoluteFilesystemPath(cleaned)) {
    const abs = cleaned.replace(/\\/g, '/');
    if (root) {
      const absLower = abs.toLowerCase();
      const rootLower = root.toLowerCase();
      if (absLower === rootLower) return [];
      if (absLower.startsWith(`${rootLower}/`)) {
        return [abs];
      }
    }
    const rel = toWorkspaceRelativePath(cleaned, root);
    if (rel && !isAbsoluteFilesystemPath(rel)) {
      return buildWorkspaceFilePathCandidates(root, rel, projectDir);
    }
    return [abs];
  }

  const rel = enrichWorkspaceRelativePath(cleaned, projectDir);
  if (!rel || isAbsoluteFilesystemPath(rel)) return [];

  if (!root) return [rel];
  const out: string[] = [];
  const add = (p: string) => {
    const n = p.replace(/\\/g, '/');
    if (n && !out.includes(n)) out.push(n);
  };
  add(`${root}/${rel.replace(/^\/+/, '')}`);
  const raw = toWorkspaceRelativePath(cleaned, root);
  if (raw && raw !== rel && !isAbsoluteFilesystemPath(raw)) {
    add(`${root}/${raw.replace(/^\/+/, '')}`);
  }
  if (projectDir) {
    const pd = projectDir.replace(/^\/+|\/+$/g, '');
    const bare = sanitizeWorkspaceChipPath(filePath).replace(/^\/+/, '');
    if (bare && !bare.startsWith(`${pd}/`) && !isAbsoluteFilesystemPath(bare)) {
      add(`${root}/${pd}/${bare}`);
    }
  }
  return out;
}

/** Try workspace project folders when project_dir is unknown. */
export function expandWorkspaceFilePathCandidates(
  workspaceRoot: string,
  filePath: string,
  projectDir: string | undefined,
  extraProjectDirs: string[] = [],
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const addAll = (list: string[]) => {
    for (const p of list) {
      const n = p.replace(/\\/g, '/');
      const key = n.toLowerCase();
      if (n && !seen.has(key)) {
        seen.add(key);
        out.push(n);
      }
    }
  };
  addAll(buildWorkspaceFilePathCandidates(workspaceRoot, filePath, projectDir));
  if (!projectDir) {
    for (const dir of extraProjectDirs) {
      const name = (dir || '').trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
      if (!name) continue;
      addAll(buildWorkspaceFilePathCandidates(workspaceRoot, filePath, name));
    }
  }
  return out;
}

/** Join workspace-relative paths from chat/workbench chips to the agent workspace root. */
export function resolvePathUnderWorkspace(
  workspaceRoot: string,
  filePath: string,
  projectDir?: string,
): string {
  const candidates = buildWorkspaceFilePathCandidates(
    workspaceRoot,
    filePath,
    projectDir,
  );
  return candidates[0] || '';
}
