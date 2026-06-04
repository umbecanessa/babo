/**
 * Run a command on a remote host over SSH (password or key via ssh2).
 */

import { Client } from 'ssh2';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

export interface SshExecOptions {
  hostname: string;
  port: number;
  username: string;
  password?: string;
  command: string;
  timeoutMs?: number;
}

function loadDefaultPrivateKeys(): Buffer[] {
  const sshDir = path.join(os.homedir(), '.ssh');
  const names = ['id_ed25519', 'id_rsa', 'id_ecdsa'];
  const keys: Buffer[] = [];
  for (const name of names) {
    const keyPath = path.join(sshDir, name);
    try {
      if (fs.existsSync(keyPath)) {
        keys.push(fs.readFileSync(keyPath));
      }
    } catch {
      /* skip unreadable keys */
    }
  }
  return keys;
}

export function execRemoteSshCommand(options: SshExecOptions): Promise<string> {
  const timeoutMs = options.timeoutMs ?? 20_000;
  const password = options.password?.trim();

  return new Promise((resolve, reject) => {
    const conn = new Client();
    let settled = false;
    const finish = (err?: Error, stdout?: string) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        conn.end();
      } catch {
        /* ignore */
      }
      if (err) reject(err);
      else resolve(stdout ?? '');
    };

    const timer = setTimeout(() => {
      finish(new Error('SSH connection timed out'));
    }, timeoutMs);

    conn.on('ready', () => {
      conn.exec(options.command, (err, stream) => {
        if (err) {
          finish(err);
          return;
        }
        let stdout = '';
        let stderr = '';
        stream.on('data', (chunk: Buffer) => {
          stdout += chunk.toString();
        });
        stream.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString();
        });
        stream.on('close', (code: number) => {
          if (code === 0) {
            finish(undefined, stdout);
          } else {
            finish(
              new Error(
                stderr.trim() ||
                  stdout.trim() ||
                  `Remote command failed (exit ${code})`,
              ),
            );
          }
        });
      });
    });

    conn.on('error', (err) => finish(err));

    const connectOpts: Record<string, unknown> = {
      host: options.hostname,
      port: options.port,
      username: options.username,
      readyTimeout: timeoutMs,
      // Homelab LAN — accept host key on first connect (like ssh StrictHostKeyChecking=accept-new)
      hostVerifier: () => true,
    };

    if (password) {
      connectOpts.password = password;
    } else {
      const keys = loadDefaultPrivateKeys();
      if (keys.length === 1) {
        connectOpts.privateKey = keys[0];
      } else if (keys.length > 1) {
        connectOpts.privateKey = keys[0];
        // ssh2 tries first key; additional keys would need agent — password path is primary fix
      }
    }

    conn.connect(connectOpts as Parameters<Client['connect']>[0]);
  });
}
