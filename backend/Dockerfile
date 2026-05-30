# Babo NestJS API — multi-stage build (dist + native deps baked in at build time)
FROM node:22-bookworm-slim AS build
WORKDIR /app

# node-pty needs node-gyp when prebuilds are missing (Alpine/musl); build once here.
RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# GITHUB_TOKEN + BILLING_PROVIDER must be set on Railway for operator builds
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
COPY --from=build /app/babo-operator ./babo-operator

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start:railway"]
