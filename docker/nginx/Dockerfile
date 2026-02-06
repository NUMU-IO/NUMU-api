# =============================================================================
# NUMU API - Custom Nginx with Brotli Module
# =============================================================================
# The official nginx:alpine image does not include the Brotli module.
# This Dockerfile compiles ngx_brotli as a dynamic module and copies it
# into the stock Alpine image, giving full control over the Nginx version.
#
# References:
#   - https://github.com/google/ngx_brotli
#   - https://github.com/nginx/docker-nginx/issues/371
#   - https://github.com/fholzer/docker-nginx-brotli
# =============================================================================

# Pin the exact Nginx version for reproducible builds
ARG NGINX_VERSION=1.25.4

# ---------------------------------------------------------------------------
# Builder: compile ngx_brotli dynamic modules
# ---------------------------------------------------------------------------
FROM nginx:${NGINX_VERSION}-alpine AS builder

RUN apk add --no-cache \
    git \
    gcc \
    libc-dev \
    make \
    openssl-dev \
    pcre2-dev \
    zlib-dev \
    linux-headers \
    cmake \
    brotli-dev

WORKDIR /tmp

# Clone ngx_brotli and its brotli submodule
RUN git clone --recurse-submodules --depth=1 \
    https://github.com/google/ngx_brotli.git

# Download the matching Nginx source to build the dynamic module against
RUN wget "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" && \
    tar -xzf nginx-${NGINX_VERSION}.tar.gz

# Build the dynamic modules (.so files)
WORKDIR /tmp/nginx-${NGINX_VERSION}
RUN ./configure \
        --with-compat \
        --add-dynamic-module=/tmp/ngx_brotli && \
    make modules

# ---------------------------------------------------------------------------
# Production: stock Nginx Alpine + compiled Brotli modules
# ---------------------------------------------------------------------------
FROM nginx:${NGINX_VERSION}-alpine

# Copy the compiled Brotli modules
COPY --from=builder /tmp/nginx-${NGINX_VERSION}/objs/ngx_http_brotli_filter_module.so \
     /etc/nginx/modules/
COPY --from=builder /tmp/nginx-${NGINX_VERSION}/objs/ngx_http_brotli_static_module.so \
     /etc/nginx/modules/

# Install brotli runtime library (needed by the dynamic modules)
RUN apk add --no-cache brotli-libs

# Copy our custom Nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Validate configuration at build time
RUN nginx -t

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
