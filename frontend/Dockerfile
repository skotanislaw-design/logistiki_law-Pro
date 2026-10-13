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

# Σφραγίδα του build στον service worker. Το όνομα της κρυφής μνήμης του (public/service-worker.js) περιέχει το
# __BUILD_ID__· με κωδικό που δεν αλλάζει ανάμεσα σε εκδόσεις, τα bytes του worker μένουν ίδια, ο browser δεν τον
# εγκαθιστά ποτέ ξανά και οι χρήστες μένουν για πάντα στην πρώτη έκδοση που άνοιξαν. Ο κωδικός είναι ο κατακερματισμός
# του index.html, που ονομάζει τα bundles με hash: αλλάζει όποτε αλλάζει ο κώδικας. Αν η αντικατάσταση δεν γίνει, ο build
# σταματά, αντί να παραδώσει σιωπηλά εικόνα με worker που δεν ενημερώνεται.
RUN BUILD_ID="$(sha256sum build/index.html | cut -c1-16)" \
    && test -n "$BUILD_ID" \
    && sed -i "s/__BUILD_ID__/${BUILD_ID}/g" build/service-worker.js \
    && ! grep -q "__BUILD_ID__" build/service-worker.js

# Nginx για static serving
FROM nginx:alpine

COPY --from=builder /app/build /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget --spider --quiet http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
