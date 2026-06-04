import { Injectable } from '@angular/core';
import { Observable, from, forkJoin, of } from 'rxjs';
import { map, switchMap } from 'rxjs/operators';
import { ApiService, FileAttachment } from './api.service';
import { PlatformService } from './platform.service';
import { ToastService } from '../../shared/toast/toast.service';
import { ParsedDrop, parseDataTransfer, parseFileList } from '../utils/chat-drop.util';

@Injectable({ providedIn: 'root' })
export class ChatAttachmentService {
  constructor(
    private api: ApiService,
    private platform: PlatformService,
    private toast: ToastService,
  ) {}

  uploadFromDataTransfer(agentId: string, dataTransfer: DataTransfer): Observable<FileAttachment[]> {
    return from(parseDataTransfer(dataTransfer)).pipe(
      switchMap((drops) => this.uploadParsed(agentId, drops)),
    );
  }

  uploadFromFileList(agentId: string, files: File[]): Observable<FileAttachment[]> {
    const drops = parseFileList(files);
    return this.uploadParsed(agentId, drops);
  }

  private uploadParsed(agentId: string, drops: ParsedDrop[]): Observable<FileAttachment[]> {
    if (!agentId || !drops.length) {
      return of([]);
    }

    const tasks = drops.map((drop) => {
      if (drop.kind === 'files') {
        const files = drop.files ?? [];
        if (!files.length) return of([] as FileAttachment[]);
        return this.api.uploadFiles(agentId, files);
      }

      const folderName = drop.folderName || 'folder';
      const files = drop.files ?? [];

      if (this.platform.isElectron && drop.folderPath) {
        return this.api.importFolder(agentId, drop.folderPath);
      }

      if (!files.length) {
        this.toast.show(`"${folderName}" is empty`, 'error');
        return of([] as FileAttachment[]);
      }

      return this.api.uploadFolder(agentId, folderName, files, drop.relativePaths ?? []);
    });

    return forkJoin(tasks).pipe(map((groups) => groups.flat()));
  }
}
