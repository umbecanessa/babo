# Babo NestJS API — multi-stage build (dist baked into image; no Railpack artifact loss)
FROM node:22-alpine AS build
WORKDIR /app

RUN apk add --no-cache git

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# GITHUB_TOKEN + BILLING_PROVIDER must be set on Railway for operator builds
ENV NODE_ENV=development
RUN node scripts/ensure-operator.js
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app

RUN apk add --no-cache git openssl

COPY package.json package-lock.json ./
COPY prisma ./prisma
COPY scripts ./scripts
COPY --from=build /app/dist ./dist
COPY --from=build /app/babo-operator ./babo-operator

RUN npm ci --omit=dev \
  && npx prisma generate \
  && if [ -f babo-operator/dist/index.js ]; then npm install ./babo-operator --omit=dev --no-audit --no-fund; fi

ENV NODE_ENV=production

EXPOSE 3000

CMD ["npm", "run", "start:railway"]
