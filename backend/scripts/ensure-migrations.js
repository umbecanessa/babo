/**
 * Railway startup: recover from failed Prisma migrations and baseline when the
 * schema was created via db push (migrations here are incremental only).
 *
 * Important: run `migrate deploy` before marking migrations as applied. Older
 * versions marked pending migrations as applied on existing DBs without running
 * SQL, which skipped new tables (e.g. product analytics).
 */
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { PrismaClient } = require('@prisma/client');

function run(cmd) {
  console.log(`[migrations] ${cmd}`);
  execSync(cmd, { stdio: 'inherit', env: process.env });
}

function runOrThrow(cmd) {
  try {
    run(cmd);
    return true;
  } catch {
    return false;
  }
}

function listMigrationDirs() {
  const dir = path.join(__dirname, '..', 'prisma', 'migrations');
  return fs
    .readdirSync(dir)
    .filter((d) => fs.statSync(path.join(dir, d)).isDirectory())
    .sort();
}

async function main() {
  const prisma = new PrismaClient();
  try {
    const failed = await prisma.$queryRaw`
      SELECT migration_name
      FROM "_prisma_migrations"
      WHERE finished_at IS NULL AND rolled_back_at IS NULL
    `;

    for (const row of failed) {
      run(`npx prisma migrate resolve --rolled-back ${row.migration_name}`);
    }

    const usersExists = await prisma.$queryRaw`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
      ) AS exists
    `;
    const hasUsers = !!usersExists[0]?.exists;

    const applied = await prisma.$queryRaw`
      SELECT migration_name
      FROM "_prisma_migrations"
      WHERE finished_at IS NOT NULL AND rolled_back_at IS NULL
    `;
    const appliedSet = new Set(applied.map((r) => r.migration_name));
    const all = listMigrationDirs();
    const pending = all.filter((m) => !appliedSet.has(m));

    if (!hasUsers) {
      console.log('[migrations] Empty database — pushing schema from prisma/schema.prisma');
      run('npx prisma db push --accept-data-loss');
      for (const m of all) {
        if (!appliedSet.has(m)) {
          run(`npx prisma migrate resolve --applied ${m}`);
        }
      }
      run('npx prisma migrate deploy');
      return;
    }

    if (pending.length > 0) {
      console.log(`[migrations] ${pending.length} pending migration(s) on existing database`);
    }

    const deployed = runOrThrow('npx prisma migrate deploy');
    if (deployed) return;

    if (pending.length === 0) {
      throw new Error('[migrations] migrate deploy failed with no pending migrations');
    }

    console.warn(
      '[migrations] migrate deploy failed; baseline resolve for legacy db-push schema, then retry',
    );
    for (const m of pending) {
      run(`npx prisma migrate resolve --applied ${m}`);
    }
    run('npx prisma migrate deploy');
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((err) => {
  console.error('[migrations] Fatal:', err.message || err);
  process.exit(1);
});
