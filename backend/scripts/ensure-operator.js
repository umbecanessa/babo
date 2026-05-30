#!/usr/bin/env node
/**
 * Clone/build private @babo/operator when BILLING_PROVIDER=operator.
 * Installs into backend/babo-operator (inside the app dir) so Railway images include it.
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const provider = process.env.BILLING_PROVIDER || 'internal';
const backendDir = path.join(__dirname, '..');
const operatorDir = path.join(backendDir, 'babo-operator');
const installedMain = path.join(
  backendDir,
  'node_modules',
  '@babo',
  'operator',
  'dist',
  'index.js',
);
const repo =
  process.env.BABO_OPERATOR_REPO ||
  'https://github.com/umbecanessa/babo-operator.git';

if (provider !== 'operator') {
  console.log('[operator] Skipping — BILLING_PROVIDER is not "operator"');
  process.exit(0);
}

function run(cmd, cwd = backendDir, extraEnv = {}) {
  execSync(cmd, {
    cwd,
    stdio: 'inherit',
    env: { ...process.env, ...extraEnv },
  });
}

const operatorPackageJson = path.join(operatorDir, 'package.json');

function refreshOperatorClone() {
  if (!fs.existsSync(path.join(operatorDir, '.git'))) return;
  console.log('[operator] Pulling latest babo-operator…');
  try {
    run('git fetch origin main --depth 1 && git reset --hard origin/main', operatorDir);
  } catch {
    console.log('[operator] Fetch failed — recloning babo-operator…');
    fs.rmSync(operatorDir, { recursive: true, force: true });
    const token = process.env.GITHUB_TOKEN || process.env.RAILWAY_GITHUB_TOKEN;
    if (!token) {
      console.error('[operator] Set GITHUB_TOKEN to refresh babo-operator.');
      process.exit(1);
    }
    const cloneUrl = repo.replace(
      'https://',
      `https://x-access-token:${token}@`,
    );
    run(`git clone --depth 1 "${cloneUrl}" "${operatorDir}"`);
  }
}

if (!fs.existsSync(operatorDir)) {
  const token = process.env.GITHUB_TOKEN || process.env.RAILWAY_GITHUB_TOKEN;
  if (!token) {
    console.error(
      '[operator] babo-operator/ missing. Set GITHUB_TOKEN (read access to umbecanessa/babo-operator).',
    );
    process.exit(1);
  }
  const cloneUrl = repo.replace(
    'https://',
    `https://x-access-token:${token}@`,
  );
  console.log('[operator] Cloning private babo-operator into backend/babo-operator…');
  run(`git clone --depth 1 "${cloneUrl}" "${operatorDir}"`);
} else {
  refreshOperatorClone();
}

if (fs.existsSync(operatorDir) && !fs.existsSync(operatorPackageJson)) {
  fs.rmSync(operatorDir, { recursive: true, force: true });
  const token = process.env.GITHUB_TOKEN || process.env.RAILWAY_GITHUB_TOKEN;
  if (!token) {
    console.error('[operator] Set GITHUB_TOKEN to refresh babo-operator.');
    process.exit(1);
  }
  const cloneUrl = repo.replace(
    'https://',
    `https://x-access-token:${token}@`,
  );
  run(`git clone --depth 1 "${cloneUrl}" "${operatorDir}"`);
}

console.log('[operator] Installing and building…');
run('npm install --include=dev --no-audit --no-fund', operatorDir, {
  NODE_ENV: 'development',
});
run('npm run build', operatorDir, { NODE_ENV: 'development' });

console.log('[operator] Linking into node_modules/@babo/operator…');
run('npm install "./babo-operator" --no-audit --no-fund');

const stripeModule = path.join(backendDir, 'node_modules', 'stripe', 'package.json');
if (!fs.existsSync(stripeModule)) {
  console.log('[operator] Installing stripe runtime dependency…');
  run('npm install stripe@^22.2.0 --no-audit --no-fund');
}

if (!fs.existsSync(installedMain)) {
  console.error('[operator] Install failed — dist/index.js not found in node_modules');
  process.exit(1);
}

console.log('[operator] Ready');
