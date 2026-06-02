import {
  buildWorkspaceFilePathCandidates,
  enrichWorkspaceRelativePath,
  isAbsoluteFilesystemPath,
  isInvalidWorkspacePathToken,
  resolvePathUnderWorkspace,
  sanitizeWorkspaceChipPath,
  toWorkspaceRelativePath,
} from './workspace-path.util';

describe('workspace-path.util', () => {
  const root =
    'C:/Users/umber/AppData/Roaming/babo-desktop/data/agents/abc/workspace';

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
