import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, from, of, forkJoin, throwError } from 'rxjs';
import { map, switchMap, catchError } from 'rxjs/operators';
import { environment } from '../../../environments/environment';
import { PlatformService } from './platform.service';

export interface FileEntry {
  name: string;
  path: string;
  isDirectory: boolean;
  size: number;
}

export interface ReadDirResponse {
  entries: FileEntry[];
  path: string;
}

export interface ReadFileResponse {
  content: string;
  metadata: {
    path: string;
    total_lines: number;
    size: number;
  };
}

export interface WriteFileResponse {
  message: string;
  metadata: {
    path: string;
    size: number;
    append: boolean;
  };
}

export interface SearchResult {
  text: string;
  metadata: {
    matches: number;
    engine: string;
  };
}

export interface FileStatResult {
  isFile: boolean;
  isDirectory: boolean;
}

/** Helper to access the Electron IPC bridge. */
function nls(): any {
  return (window as any).nls;
}

@Injectable({ providedIn: 'root' })
export class FilesystemService {
  private readonly API = environment.apiUrl;

  constructor(
    private http: HttpClient,
    private platform: PlatformService,
  ) {}

  /** Read immediate directory contents for the file explorer. */
  readDir(dirPath: string): Observable<ReadDirResponse> {
    // In Electron, use native IPC for local filesystem access
    if (this.platform.isElectron && nls()?.readDir) {
      return from(
        nls().readDir(dirPath) as Promise<
          Array<{ name: string; isDirectory: boolean; size: number }>
        >,
      ).pipe(
        map((entries) => ({
          path: dirPath,
          entries: entries
            .map((e) => ({
              name: e.name,
              path: this.joinPath(dirPath, e.name),
              isDirectory: e.isDirectory,
              size: e.size || 0,
            }))
            .sort((a, b) => {
              // Directories first, then alphabetical
              if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
              return a.name.localeCompare(b.name);
            }),
        })),
      );
    }

    // Fallback: HTTP backend (browser / cloud mode)
    return this.http.get<ReadDirResponse>(`${this.API}/fs/readdir`, {
      params: { path: dirPath },
    });
  }

  /** File vs directory (Electron IPC; HTTP fallback via readdir parent). */
  stat(filePath: string): Observable<FileStatResult> {
    if (this.platform.isElectron && nls()?.stat) {
      return from(
        nls().stat(filePath) as Promise<FileStatResult>,
      );
    }

    const name = filePath.split(/[/\\]/).pop() || '';
    const parent = filePath.replace(/[/\\][^/\\]+$/, '');
    if (!parent || parent === filePath) {
      return of({ isFile: false, isDirectory: true });
    }
    return this.readDir(parent).pipe(
      map((res) => {
        const entry = res.entries.find(
          (e) => e.path === filePath || e.name === name,
        );
        return {
          isFile: !!entry && !entry.isDirectory,
          isDirectory: !!entry?.isDirectory,
        };
      }),
    );
  }

  /** Read a file. */
  readFile(filePath: string, offset = 0, limit = 0): Observable<ReadFileResponse> {
    if (this.platform.isElectron && nls()?.readFile) {
      return from(nls().readFile(filePath) as Promise<string>).pipe(
        map((content) => ({
          content,
          metadata: {
            path: filePath,
            total_lines: content.split('\n').length,
            size: content.length,
          },
        })),
      );
    }

    let params = new HttpParams().set('path', filePath);
    if (offset > 0) params = params.set('offset', String(offset));
    if (limit > 0) params = params.set('limit', String(limit));
    return this.http.get<ReadFileResponse>(`${this.API}/fs/read`, { params });
  }

  /** Write or create a file. */
  writeFile(filePath: string, content: string, append = false): Observable<WriteFileResponse> {
    if (this.platform.isElectron && nls()?.writeFile) {
      return from(nls().writeFile(filePath, content) as Promise<void>).pipe(
        map(() => ({
          message: 'File written',
          metadata: { path: filePath, size: content.length, append },
        })),
      );
    }

    return this.http.post<WriteFileResponse>(`${this.API}/fs/write`, {
      path: filePath,
      content,
      append,
    });
  }

  /** Create a directory (optionally recursive). */
  mkdir(dirPath: string, recursive = true): Observable<{ message: string; path: string }> {
    if (this.platform.isElectron) {
      if (nls()?.mkdir) {
        return from(nls().mkdir(dirPath, recursive) as Promise<void>).pipe(
          map(() => ({ message: 'Directory created', path: dirPath })),
        );
      }
      return throwError(() => new Error('Folder creation requires a desktop app update'));
    }

    return this.http.post<{ message: string; path: string }>(`${this.API}/fs/mkdir`, {
      path: dirPath,
      recursive,
    });
  }

  /** Write binary file from base64 payload. */
  writeFileBytes(filePath: string, contentBase64: string): Observable<WriteFileResponse> {
    if (this.platform.isElectron) {
      if (nls()?.writeFileBytes) {
        return from(nls().writeFileBytes(filePath, contentBase64) as Promise<void>).pipe(
          map(() => ({
            message: 'File written',
            metadata: { path: filePath, size: 0, append: false },
          })),
        );
      }
      return throwError(() => new Error('Binary upload requires a desktop app update'));
    }

    return this.http.post<WriteFileResponse>(`${this.API}/fs/write-bytes`, {
      path: filePath,
      content_base64: contentBase64,
    });
  }

  /** Upload browser File objects into a workspace directory. */
  uploadFiles(targetDir: string, files: File[]): Observable<void> {
    if (!files.length) {
      return of(undefined);
    }

    const tasks = files.map((file) => {
      const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      const destPath = this.joinPathSegments(targetDir, rel);
      const parent = destPath.replace(/[/\\][^/\\]+$/, '');

      const write$ = file.type.startsWith('text/') || this.isLikelyTextFile(file.name)
        ? from(file.text()).pipe(
            switchMap((content) => this.writeFile(destPath, content)),
          )
        : from(file.arrayBuffer()).pipe(
            switchMap((buf) => this.writeFileBytes(destPath, this.arrayBufferToBase64(buf))),
          );

      if (parent && parent !== destPath) {
        return this.mkdir(parent, true).pipe(
          catchError(() => of(null)),
          switchMap(() => write$),
        );
      }
      return write$;
    });

    return forkJoin(tasks).pipe(map(() => undefined));
  }

  /** Get a directory tree (text representation). */
  getTree(path: string, depth = 3, glob = ''): Observable<any> {
    let params = new HttpParams().set('path', path).set('depth', String(depth));
    if (glob) params = params.set('glob', glob);
    return this.http.get(`${this.API}/fs/tree`, { params });
  }

  /** Perform surgical text replacement in a file. */
  editFile(
    path: string,
    oldString: string,
    newString: string,
    replaceAll = false,
  ): Observable<any> {
    return this.http.post(`${this.API}/fs/edit`, {
      path,
      old_string: oldString,
      new_string: newString,
      replace_all: replaceAll,
    });
  }

  /** Search file contents using ripgrep/Python fallback. */
  searchFiles(
    pattern: string,
    path: string,
    glob = '',
    maxResults = 50,
  ): Observable<SearchResult> {
    let params = new HttpParams()
      .set('pattern', pattern)
      .set('path', path)
      .set('max_results', String(maxResults));
    if (glob) params = params.set('glob', glob);
    return this.http.get<SearchResult>(`${this.API}/fs/search`, { params });
  }

  /** Join one segment to a base path. */
  joinPath(base: string, name: string): string {
    return this.joinPathSegments(base, name);
  }

  /** Join base + relative path (may contain nested segments). */
  joinPathSegments(base: string, relative: string): string {
    const sep = base.includes('\\') ? '\\' : '/';
    const normalized = (relative || '').replace(/\\/g, sep);
    const parts = normalized.split(sep).filter((p) => p && p !== '.' && p !== '..');
    let current = base.replace(/[/\\]+$/, '');
    for (const part of parts) {
      current = current.endsWith(sep) ? current + part : current + sep + part;
    }
    return current;
  }

  private arrayBufferToBase64(buf: ArrayBuffer): string {
    const bytes = new Uint8Array(buf);
    const chunkSize = 0x8000;
    let binary = '';
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
  }

  private isLikelyTextFile(name: string): boolean {
    const ext = name.split('.').pop()?.toLowerCase() || '';
    const textExts = new Set([
      'txt', 'md', 'json', 'yaml', 'yml', 'xml', 'html', 'htm', 'css', 'scss',
      'js', 'jsx', 'ts', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h',
      'cs', 'php', 'sh', 'bat', 'ps1', 'sql', 'toml', 'ini', 'env', 'csv', 'svg',
    ]);
    return textExts.has(ext) || !ext;
  }
}
