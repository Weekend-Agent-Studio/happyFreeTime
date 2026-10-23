FROM node:24-bookworm-slim AS build
ARG PNPM_VERSION=11.25.0
ARG HFT_REVISION=development
ENV VITE_PUBLIC_DEMO=true \
    VITE_SHOW_DEVELOPER_DETAILS=false \
    NODE_OPTIONS=--max-old-space-size=1024
WORKDIR /build/frontend
RUN npm install --global pnpm@${PNPM_VERSION}
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
COPY frontend/src/ ./src/
COPY frontend/public/ ./public/
RUN pnpm build \
    && node -e "require('node:fs').writeFileSync('dist/version.json', JSON.stringify({release: process.env.HFT_REVISION}) + '\n')"

FROM nginx:stable-alpine
ARG HFT_REVISION=development
LABEL org.opencontainers.image.source="https://github.com/Weekend-Agent-Studio/happyFreeTime" \
      org.opencontainers.image.revision="${HFT_REVISION}"
COPY --from=build /build/frontend/dist/ /usr/share/nginx/html/
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/site.http.conf /etc/nginx/conf.d/default.conf
COPY deploy/nginx/routes.conf /etc/nginx/hft/routes.conf
COPY deploy/nginx/proxy.conf /etc/nginx/hft/proxy.conf
USER 101:101
EXPOSE 8080 8443
CMD ["nginx", "-g", "daemon off;"]
