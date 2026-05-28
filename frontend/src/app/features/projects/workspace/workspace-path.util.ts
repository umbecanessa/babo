import { normalizeWorkbenchFilePath } from '../../../core/services/activity-format.util';

/** Resolve the on-disk workspace root for an agent (desktop shell). */
export function resolveAgentWorkspacePath(agentId: string): string {
  if (!agentId) return '';

  const nls = (window as { nls?: { getDataPath?: () => string } }).nls;
  const base = nls?.getDataPath?.()
    ? `${nls.getDataPath()}/agents/${agentId}/workspace`
    : `C:/Users/umber/AppData/Roaming/babo-desktop/data/agents/${agentId}/workspace`;

  return base.replace(/\\/g, '/');
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
