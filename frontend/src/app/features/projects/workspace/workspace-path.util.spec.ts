import {
  buildWorkspaceFilePathCandidates,
  enrichWorkspaceRelativePath,
  expandWorkspaceFilePathCandidates,
  isAbsoluteFilesystemPath,
  isInvalidWorkspacePathToken,
  resolvePathUnderWorkspace,
  sanitizeWorkspaceChipPath,
  toWorkspaceRelativePath,
  resolveAgentWorkspacePath,
} from './workspace-path.util';

describe('workspace-path.util', () => {
  const root =
    'C:/Users/umber/AppData/Roaming/babo-desktop/data/agents/abc/workspace';

  it('resolveAgentWorkspacePath uses nls data path when available', () => {
    (window as any).nls = { getDataPath: () => 'D:/babo-data' };
    expect(resolveAgentWorkspacePath('abc')).toBe('D:/babo-data/agents/abc/workspace');
    delete (window as any).nls;
  });

  it('resolveAgentWorkspacePath returns empty without data path', () => {
    delete (window as any).nls;
    expect(resolveAgentWorkspacePath('abc')).toBe('');
  });

  it('detects absolute Windows paths', () => {
    expect(isAbsoluteFilesystemPath('C:\\foo\\bar.ts')).toBe(true);
    expect(isAbsoluteFilesystemPath('backend/src/index.ts')).toBe(false);
    expect(isAbsoluteFilesystemPath('/')).toBe(false);
  });

  it('rejects root and empty chip paths', () => {
    expect(isInvalidWorkspacePathToken('/')).toBe(true);
    expect(sanitizeWorkspaceChipPath('/')).toBe('');
    expect(buildWorkspaceFilePathCandidates(root, '/')).toEqual([]);
  });

  it('joins relative chip paths to workspace root', () => {
    const rel = 'icf-coaching-evaluation-platform-stack/backend/src/index.ts';
    expect(resolvePathUnderWorkspace(root, rel)).toBe(`${root}/${rel}`);
  });

  it('leaves absolute paths unchanged', () => {
    const abs = `${root}/backend/src/index.ts`;
    expect(resolvePathUnderWorkspace(root, abs)).toBe(abs);
  });

  it('prefixes bare paths with project_dir', () => {
    expect(enrichWorkspaceRelativePath('.gitignore', 'icf-coaching-platform')).toBe(
      'icf-coaching-platform/.gitignore',
    );
  });

  it('tries project_dir path when opening files', () => {
    const candidates = buildWorkspaceFilePathCandidates(
      root,
      '.gitignore',
      'icf-coaching-platform',
    );
    expect(candidates).toContain(`${root}/icf-coaching-platform/.gitignore`);
  });

  it('expands candidates across workspace project folders', () => {
    const candidates = expandWorkspaceFilePathCandidates(
      root,
      'backend/app/models/transcript.py',
      undefined,
      ['ai-powered-icf-coaching-session'],
    );
    expect(candidates).toContain(
      `${root}/ai-powered-icf-coaching-session/backend/app/models/transcript.py`,
    );
  });

  it('does not double-prefix absolute explorer paths under workspace', () => {
    const abs = `${root}/icf-coaching-evaluation-platform/backend/requirements.txt`;
    const candidates = buildWorkspaceFilePathCandidates(
      root,
      abs,
      'icf-coaching-evaluation-platform',
    );
    expect(candidates).toEqual([abs]);
    expect(candidates[0]).not.toContain(`${root}/${root}`);
  });

  it('normalizes Windows absolute paths to workspace-relative', () => {
    const abs =
      'C:\\Users\\umber\\AppData\\Roaming\\babo-desktop\\data\\agents\\abc\\workspace\\icf-coaching\\backend\\package.json';
    const rel = toWorkspaceRelativePath(abs, root);
    expect(rel).toBe('icf-coaching/backend/package.json');
  });
});
