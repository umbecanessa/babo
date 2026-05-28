import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, from, of } from 'rxjs';
import { map } from 'rxjs/operators';
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

  /** Join path segments (works on both Windows and Unix). */
  private joinPath(base: string, name: string): string {
    const sep = base.includes('\\') ? '\\' : '/';
    return base.endsWith(sep) ? base + name : base + sep + name;
  }
}
