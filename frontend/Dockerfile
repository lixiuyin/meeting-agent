ARG NODE_IMAGE=node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0
ARG NGINX_IMAGE=nginx:alpine@sha256:72ba65eb42c10344912a84ff42408db7d34f2feb642204570ab8fc5ffd29f1d3
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install -g npm@11.12.1 && npm ci --legacy-peer-deps
COPY . .
RUN npm run build

# Use the official nginx alpine image. Brotli pre-compression is dropped
# (the previous third-party fholzer/nginx-brotli:alpine has been removed
# from Docker Hub); gzip_static still works on the pre-compressed .gz
# files generated below, which covers the largest CDN-friendly wins.
FROM ${NGINX_IMAGE}
# Apply available distribution security updates to the pinned base image.
RUN apk upgrade --no-cache
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY security-headers.conf /etc/nginx/snippets/meeting-agent-security-headers.conf
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chown -R nginx:nginx /etc/nginx/conf.d /etc/nginx/templates /etc/nginx/snippets && \
    chown -R nginx:nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid && \
    # Pre-compress static assets for gzip_static
    find /usr/share/nginx/html -type f \( -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "*.json" -o -name "*.svg" -o -name "*.txt" \) -exec gzip -9 -k {} \;

# Keep the upstream nginx entrypoint so its template envsubst hook renders
# /etc/nginx/templates/default.conf.template.  The upstream entrypoint executes
# *.sh hooks in child processes but sources *.envsh hooks, so this validation
# hook must be an envsh file: the FRONTEND_AUTH_MODE/API_KEY exports need to be
# visible to the later 20-envsubst-on-templates.sh hook.
COPY docker-entrypoint.sh /docker-entrypoint.d/05-validate-backend-api-key.envsh
RUN chmod +x /docker-entrypoint.d/05-validate-backend-api-key.envsh
USER nginx
CMD ["nginx", "-g", "daemon off;"]

EXPOSE 8080
