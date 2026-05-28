FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG BABO_ADMIN_API=/api
RUN sed -i "s|<body>|<body><script>window.__BABO_ADMIN_API__='${BABO_ADMIN_API}';</script>|" src/index.html
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist/admin/browser ./browser
CMD sh -c "serve browser -s -l ${PORT:-3000}"
