FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install -g npm@11.12.1 && npm ci --legacy-peer-deps
COPY . .
RUN npm run build

# Use the official nginx alpine image. Brotli pre-compression is dropped
# (the previous third-party fholzer/nginx-brotli:alpine has been removed
# from Docker Hub); gzip_static still works on the pre-compressed .gz
# files generated below, which covers the largest CDN-friendly wins.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chown -R nginx:nginx /var/cache/nginx && \
    chown -R nginx:nginx /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown -R nginx:nginx /var/run/nginx.pid && \
    # Pre-compress static assets for gzip_static
    find /usr/share/nginx/html -type f \( -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "*.json" -o -name "*.svg" -o -name "*.txt" \) -exec gzip -9 -k {} \;

# Validate BACKEND_API_KEY at container start to avoid silent 403 failures
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
USER nginx
CMD ["nginx", "-g", "daemon off;"]

EXPOSE 8080
