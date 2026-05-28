FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist/admin/browser ./browser
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENV BABO_ADMIN_API=https://api.babo.agency/api
EXPOSE 3000
ENTRYPOINT ["/docker-entrypoint.sh"]
