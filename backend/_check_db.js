const { PrismaClient } = require('@prisma/client');
const p = new PrismaClient();

async function main() {
  const aliases = await p.channelAlias.findMany();
  console.log('=== CHANNEL ALIASES ===');
  console.log(JSON.stringify(aliases, null, 2));

  const pending = await p.pendingChannelMessage.findMany({
    orderBy: { createdAt: 'desc' },
    take: 10,
  });
  console.log('\n=== PENDING MESSAGES ===');
  console.log(JSON.stringify(pending, null, 2));

  await p.$disconnect();
}

main().catch(e => { console.error(e); process.exit(1); });
