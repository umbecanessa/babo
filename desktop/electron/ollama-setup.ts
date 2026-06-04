/**
 * Ollama detection and model pull for onboarding Model Fit setup.
 */

import { execFile, spawn } from 'child_process';
import { promisify } from 'util';

import { testOllamaEndpoint } from './capability-scanner';

const execFileAsync = promisify(execFile);

const DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434';

export interface OllamaStatus {
  installed: boolean;
  running: boolean;
  url: string;
  message: string;
  models: string[];
}

export async function getOllamaStatus(
  baseUrl = DEFAULT_OLLAMA_URL,
): Promise<OllamaStatus> {
  let installed = false;
  try {
    await execFileAsync(
      process.platform === 'win32' ? 'where' : 'which',
      ['ollama'],
      { timeout: 5_000, windowsHide: true },
    );
    installed = true;
  } catch {
    installed = false;
  }

  const probe = await testOllamaEndpoint(baseUrl);
  return {
    installed,
    running: probe.ok,
    url: baseUrl,
    message: probe.message,
    models: probe.models,
  };
}

export async function pullOllamaModel(
  modelTag: string,
  onLine?: (line: string) => void,
): Promise<{ ok: boolean; message: string }> {
  const tag = modelTag.trim();
  if (!tag) {
    return { ok: false, message: 'No model specified' };
  }

  return new Promise((resolve) => {
    const proc = spawn('ollama', ['pull', tag], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    const handle = (chunk: Buffer) => {
      const text = chunk.toString();
      text.split('\n').filter(Boolean).forEach((line) => onLine?.(line));
    };

    proc.stdout?.on('data', handle);
    proc.stderr?.on('data', handle);

    proc.on('error', (err) => {
      resolve({
        ok: false,
        message:
          err.message?.includes('ENOENT')
            ? 'Ollama not found — install it from ollama.com first'
            : err.message || 'Failed to run ollama pull',
      });
    });

    proc.on('close', (code) => {
      if (code === 0) {
        resolve({ ok: true, message: `Downloaded ${tag}` });
      } else {
        resolve({ ok: false, message: `ollama pull exited with code ${code ?? 'unknown'}` });
      }
    });
  });
}

export const OLLAMA_DOWNLOAD_URL = 'https://ollama.com/download';
