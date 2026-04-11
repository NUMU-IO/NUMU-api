# Isolated build container for external theme bundles.
#
# Runs `npm install --ignore-scripts && npm run build` on a theme source
# mounted at /theme-src, writing output to /theme-dist.
#
# Security:
# - Based on slim distroless-ish node image
# - Unprivileged user (uid 1000)
# - No network access (launched with --network=none by the Celery task)
# - Read-only theme source mount
# - Memory + CPU limits enforced by the docker run command
# - acorn is bundled so AST security scanning can run in the same image

FROM node:20-slim

# Install acorn globally for the AST scanner that runs post-build
RUN npm install -g acorn@8

# Create unprivileged user
RUN groupadd --gid 1000 numu && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash numu

# Prepare working directory
RUN mkdir -p /workspace /theme-src /theme-dist && \
    chown -R numu:numu /workspace /theme-dist

USER numu
WORKDIR /workspace

# Entrypoint: copy source to a writable location, install, build, output to /theme-dist
COPY --chown=numu:numu entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

ENTRYPOINT ["/workspace/entrypoint.sh"]
