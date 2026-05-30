#!/usr/bin/env node
/**
 * Railpack deploy images sometimes omit dist/. Rebuild at start if missing.
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const backendDir = path.join(__dirname, '..');
const mainJs = path.join(backendDir, 'dist', 'main.js');

if (fs.existsSync(mainJs)) {
  console.log('[build] dist/main.js present');
  process.exit(0);
}

console.warn('[build] dist/main.js missing — compiling Nest app at startup…');
execSync('npm install --include=dev --no-audit --no-fund', {
  cwd: backendDir,
  stdio: 'inherit',
  env: process.env,
});
execSync('npx nest build', {
  cwd: backendDir,
  stdio: 'inherit',
  env: process.env,
});

if (!fs.existsSync(mainJs)) {
  console.error('[build] nest build finished but dist/main.js is still missing');
  process.exit(1);
}

console.log('[build] dist/main.js ready');
