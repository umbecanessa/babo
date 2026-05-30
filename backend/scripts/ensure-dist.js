#!/usr/bin/env node
/**
 * Fallback when deploy image omits dist/ (Railpack). Docker images should skip this.
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const backendDir = path.join(__dirname, '..');
const candidates = [
  path.join(backendDir, 'dist', 'main.js'),
  path.join(backendDir, 'dist', 'src', 'main.js'),
];

const mainJs = candidates.find((p) => fs.existsSync(p));
if (mainJs) {
  console.log(`[build] ${path.relative(backendDir, mainJs)} present`);
  process.exit(0);
}

const srcMain = path.join(backendDir, 'src', 'main.ts');
if (!fs.existsSync(srcMain)) {
  console.error(
    '[build] dist/ is missing and src/ is not in this container — cannot compile at runtime.',
  );
  console.error('[build] Deploy with backend/Dockerfile so dist/ is copied from the build stage.');
  process.exit(1);
}

console.warn('[build] dist/main.js missing — compiling Nest app at startup…');

const env = { ...process.env, NODE_ENV: 'development' };

execSync('npm install --include=dev --no-audit --no-fund', {
  cwd: backendDir,
  stdio: 'inherit',
  env,
});

execSync('npm run build', {
  cwd: backendDir,
  stdio: 'inherit',
  env,
});

const built = candidates.find((p) => fs.existsSync(p));
if (!built) {
  const distDir = path.join(backendDir, 'dist');
  const listing = fs.existsSync(distDir)
    ? fs.readdirSync(distDir, { recursive: true }).slice(0, 20).join(', ')
    : '(dist/ does not exist)';
  console.error('[build] npm run build finished but main.js is still missing');
  console.error(`[build] dist contents (partial): ${listing}`);
  process.exit(1);
}

console.log(`[build] ${path.relative(backendDir, built)} ready`);
