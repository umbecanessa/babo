import { Injectable, BadRequestException } from '@nestjs/common';
import { RuntimeService } from '../runtime/runtime.service';

@Injectable()
export class FilesystemService {
  constructor(private runtime: RuntimeService) {}

  async getTree(path: string, depth = 3, glob = ''): Promise<any> {
    const params = new URLSearchParams({ path, depth: String(depth) });
    if (glob) params.set('glob', glob);
    return this.proxyGet(`/fs/tree?${params}`);
  }

  async readFile(path: string, offset = 0, limit = 0): Promise<any> {
    const params = new URLSearchParams({ path });
    if (offset > 0) params.set('offset', String(offset));
    if (limit > 0) params.set('limit', String(limit));
    return this.proxyGet(`/fs/read?${params}`);
  }

  async writeFile(path: string, content: string, append = false): Promise<any> {
    return this.proxyPost('/fs/write', { path, content, append });
  }

  async editFile(
    path: string,
    oldString: string,
    newString: string,
    replaceAll = false,
  ): Promise<any> {
    return this.proxyPost('/fs/edit', {
      path,
      old_string: oldString,
      new_string: newString,
      replace_all: replaceAll,
    });
  }

  async searchFiles(
    pattern: string,
    path: string,
    glob = '',
    maxResults = 50,
  ): Promise<any> {
    const params = new URLSearchParams({ pattern, path, max_results: String(maxResults) });
    if (glob) params.set('glob', glob);
    return this.proxyGet(`/fs/search?${params}`);
  }

  async readDir(path: string): Promise<any> {
    return this.proxyGet(`/fs/readdir?path=${encodeURIComponent(path)}`);
  }

  // ── Proxy helpers ──────────────────────────────────────────────

  private async proxyGet(path: string): Promise<any> {
    try {
      return await this.runtime.proxyGet(path);
    } catch (err: any) {
      throw new BadRequestException(err.message || 'Runtime filesystem error');
    }
  }

  private async proxyPost(path: string, body: any): Promise<any> {
    try {
      return await this.runtime.proxyPost(path, body);
    } catch (err: any) {
      throw new BadRequestException(err.message || 'Runtime filesystem error');
    }
  }
}
