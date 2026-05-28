import { PrismaClient } from '@prisma/client';

const p = new PrismaClient();
const cols = await p.$queryRaw`
  SELECT column_name FROM information_schema.columns
  WHERE table_name = 'agents' AND column_name LIKE '%agent_id%'`;
const tables = await p.$queryRaw`
  SELECT table_name FROM information_schema.tables
  WHERE table_schema = 'public' ORDER BY table_name`;
const mig = await p.$queryRaw`
  SELECT migration_name FROM _prisma_migrations ORDER BY finished_at`.catch(
  () => [],
);
console.log('agent columns:', cols);
console.log('tables:', tables.map((t) => t.table_name));
console.log('migrations:', mig);
await p.$disconnect();
