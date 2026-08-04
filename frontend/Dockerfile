# Multi-stage build για React app
FROM node:20-alpine AS builder

WORKDIR /app

COPY frontend/package.json frontend/yarn.lock ./
RUN yarn install --frozen-lockfile

COPY frontend/ ./

# Build args για production URL
ARG REACT_APP_BACKEND_URL
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL

RUN yarn build

# Nginx για static serving
FROM nginx:alpine

COPY --from=builder /app/build /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget --spider --quiet http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
