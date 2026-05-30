#!/usr/bin/env node
/**
 * Clone/build private @babo/operator when BILLING_PROVIDER=operator.
 * OSS self-host builds skip this (internal/noop billing).
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const provider = process.env.BILLING_PROVIDER || 'internal';
const backendDir = path.join(__dirname, '..');
const operatorDir = path.join(backendDir, '..', 'babo-operator');
const repo = process.env.BABO_OPERATOR_REPO || 'https://github.com/umbecanessa/babo-operator.git';

if (provider !== 'operator') {
  console.log('[operator] Skipping — BILLING_PROVIDER is not "operator"');
  process.exit(0);
}

if (!fs.existsSync(operatorDir)) {
  const token = process.env.GITHUB_TOKEN || process.env.RAILWAY_GITHUB_TOKEN;
  if (!token) {
    console.error(
      '[operator] ../babo-operator missing. Clone it locally or set GITHUB_TOKEN for Railway.',
    );
    process.exit(1);
  }
  const cloneUrl = repo.replace(
    'https://',
    `https://x-access-token:${token}@`,
  );
  console.log('[operator] Cloning private babo-operator…');
  execSync(`git clone --depth 1 "${cloneUrl}" "${operatorDir}"`, {
    stdio: 'inherit',
  });
}

console.log('[operator] Installing and building…');
execSync('npm install', { cwd: operatorDir, stdio: 'inherit' });
execSync('npm run build', { cwd: operatorDir, stdio: 'inherit' });
execSync('npm install "file:../babo-operator"', {
  cwd: backendDir,
  stdio: 'inherit',
});
