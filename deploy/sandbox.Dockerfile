# OpenMausBot bot sandbox image (for later: this box has no Docker).
# shell + Python + Playwright + OpenCode (coding agent harness) + the OpenMausBot CLI.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
ARG OPENMAUSBOT_VERSION=0.1.154
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN npm i -g "openmausbot@${OPENMAUSBOT_VERSION}" && curl -fsSL https://opencode.ai/install | bash
RUN pip install --no-cache-dir "mcp>=1.10,<2" pyyaml pytest
# non-root user; the workspace (git worktree) is the only writable mount
RUN useradd -m -u 1000 bot
USER bot
ENV PATH="/home/bot/.opencode/bin:${PATH}"
WORKDIR /workspace
# The permission gate is enforced by the opencode plugin in /workspace/.opencode/plugins/
# (installed by scripts/workspace/create_worktree.sh) and by the control repo's MCP tools.
CMD ["openmausbot", "serve", "--local", "--no-open", "--no-pair"]
