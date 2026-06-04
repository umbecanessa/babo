/** Parsed drag/drop payload for chat attachments. */
export interface ParsedDrop {
  kind: 'files' | 'folder';
  files?: File[];
  relativePaths?: string[];
  folderName?: string;
  /** Electron native absolute path to a dropped folder. */
  folderPath?: string;
}

type FileWithPath = File & { path?: string; webkitRelativePath?: string };

function readFileEntry(entry: FileSystemFileEntry): Promise<File | null> {
  return new Promise((resolve) => {
    entry.file((file) => resolve(file), () => resolve(null));
  });
}

function readAllEntries(dir: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  return new Promise((resolve) => {
    const reader = dir.createReader();
    const all: FileSystemEntry[] = [];
    const readBatch = () => {
      reader.readEntries(
        (batch) => {
          if (!batch.length) {
            resolve(all);
            return;
          }
          all.push(...batch);
          readBatch();
        },
        () => resolve(all),
      );
    };
    readBatch();
  });
}

async function readDirectoryEntry(
  dir: FileSystemDirectoryEntry,
  pathPrefix = '',
): Promise<{ files: File[]; relativePaths: string[]; folderName: string }> {
  const files: File[] = [];
  const relativePaths: string[] = [];
  const entries = await readAllEntries(dir);

  for (const entry of entries) {
    if (entry.isFile) {
      const file = await readFileEntry(entry as FileSystemFileEntry);
      if (!file) continue;
      files.push(file);
      relativePaths.push(pathPrefix ? `${pathPrefix}/${entry.name}` : entry.name);
    } else if (entry.isDirectory) {
      const sub = await readDirectoryEntry(
        entry as FileSystemDirectoryEntry,
        pathPrefix ? `${pathPrefix}/${entry.name}` : entry.name,
      );
      files.push(...sub.files);
      relativePaths.push(...sub.relativePaths);
    }
  }

  return { files, relativePaths, folderName: dir.name };
}

function groupFilesByFolder(files: File[]): ParsedDrop[] {
  const groups = new Map<string, { files: File[]; paths: string[] }>();
  const loose: File[] = [];

  for (const file of files) {
    const rel = (file as FileWithPath).webkitRelativePath;
    if (rel && rel.includes('/')) {
      const parts = rel.split('/');
      const folder = parts[0];
      const inner = parts.slice(1).join('/');
      const group = groups.get(folder) ?? { files: [], paths: [] };
      group.files.push(file);
      group.paths.push(inner);
      groups.set(folder, group);
    } else {
      loose.push(file);
    }
  }

  const results: ParsedDrop[] = [];
  if (loose.length) {
    results.push({ kind: 'files', files: loose });
  }
  for (const [folderName, group] of groups) {
    results.push({
      kind: 'folder',
      files: group.files,
      relativePaths: group.paths,
      folderName,
    });
  }
  return results;
}

/** Collect file/folder payloads from a drag-and-drop DataTransfer. */
export async function parseDataTransfer(dataTransfer: DataTransfer): Promise<ParsedDrop[]> {
  const items = dataTransfer.items;
  if (items?.length) {
    const results: ParsedDrop[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind !== 'file') continue;

      const entry = item.webkitGetAsEntry?.() ?? null;
      if (entry?.isDirectory) {
        const { files, relativePaths, folderName } = await readDirectoryEntry(
          entry as FileSystemDirectoryEntry,
        );
        const nativeFile = item.getAsFile() as FileWithPath | null;
        results.push({
          kind: 'folder',
          files,
          relativePaths,
          folderName,
          folderPath: nativeFile?.path,
        });
        continue;
      }

      if (entry?.isFile) {
        const file = await readFileEntry(entry as FileSystemFileEntry);
        if (file) {
          results.push({ kind: 'files', files: [file] });
        }
        continue;
      }

      const fallback = item.getAsFile();
      if (fallback) {
        results.push({ kind: 'files', files: [fallback] });
      }
    }

    if (results.length) {
      return mergeFileDrops(results);
    }
  }

  const files = Array.from(dataTransfer.files || []);
  if (!files.length) return [];
  return groupFilesByFolder(files);
}

/** Group loose file drops from the same picker/drop into one batch. */
export function parseFileList(files: File[]): ParsedDrop[] {
  if (!files.length) return [];
  const grouped = groupFilesByFolder(files);
  return mergeFileDrops(grouped);
}

function mergeFileDrops(drops: ParsedDrop[]): ParsedDrop[] {
  const loose: File[] = [];
  const folders: ParsedDrop[] = [];

  for (const drop of drops) {
    if (drop.kind === 'folder') {
      folders.push(drop);
    } else if (drop.files?.length) {
      loose.push(...drop.files);
    }
  }

  const merged: ParsedDrop[] = [];
  if (loose.length) {
    merged.push({ kind: 'files', files: loose });
  }
  merged.push(...folders);
  return merged;
}

export function isFolderAttachment(att: { mime_type?: string; is_folder?: boolean }): boolean {
  return !!att.is_folder || att.mime_type === 'application/x-directory';
}
