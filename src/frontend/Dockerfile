FROM node:22-alpine AS assets
WORKDIR /build
COPY package.json package-lock.json .npmrc build.mjs ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm run build

FROM nginx:1.29-alpine

COPY index.html app.js markdown.js styles.css /usr/share/nginx/html/
COPY --from=assets /build/vendor /usr/share/nginx/html/vendor
COPY default.conf.template /etc/nginx/templates/default.conf.template

RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/run

EXPOSE 8080
