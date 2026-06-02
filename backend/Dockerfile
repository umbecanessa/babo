# Babo NestJS API — multi-stage build (dist + native deps baked in at build time)
FROM node:22-bookworm-slim AS build
WORKDIR /app

# node-pty needs node-gyp when prebuilds are missing; compile once in the build stage.
RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Railway service env vars are available at Docker build time.
ENV NODE_ENV=development

RUN node scripts/ensure-operator.js
RUN npm run build
RUN if [ -f babo-operator/dist/index.js ]; then npm install ./babo-operator --no-audit --no-fund; fi \
  && npm prune --omit=dev

FROM node:22-bookworm-slim AS runtime
WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates openssl \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
COPY prisma ./prisma
COPY scripts ./scripts
COPY --from=build /app/dist ./dist
COPY --from=build /app/node_modules ./node_modules

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start:railway"]
