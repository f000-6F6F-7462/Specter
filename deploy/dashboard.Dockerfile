# The dashboard as one image: fa-server, which also serves the built fa-client from the same
# origin. deploy/compose.dashboard.yaml builds it with the dashboard/ submodule as its context.
# It lives here rather than in the submodule because it joins two repositories into one product.

FROM node:22-alpine AS client
WORKDIR /client
COPY client/package.json client/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY client/ ./
# .env.production points the client at a hosted API; this image serves the API from the same
# origin, which is what an empty base URL means. Only the bundle is built: type checking is the
# client's CI job (npm run build), and its test files must not stop an image from being built.
RUN rm -f .env.production && npx vite build

FROM node:22-alpine AS server
WORKDIR /server
COPY server/package.json server/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY server/ ./
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    PORT=12113 \
    CLIENT_DIST_DIR=/app/public
COPY server/package.json server/package-lock.json ./
# `prepare` installs git hooks with husky, a development dependency that is not installed here.
RUN npm pkg delete scripts.prepare && npm ci --omit=dev --no-audit --no-fund \
    && npm cache clean --force
COPY --from=server /server/dist ./dist
COPY --from=client /client/dist ./public
USER node
EXPOSE 12113
CMD ["node", "dist/index.js"]
