FROM node:24-bookworm-slim

ARG GITHUB_MCP_VERSION=v1.7.0
ARG TARGETARCH=amd64

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=development \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        openssh-client \
        jq \
        ripgrep \
        python3 \
        python3-venv \
        python3-pip \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN curl -fsSL \
      "https://github.com/github/github-mcp-server/releases/download/${GITHUB_MCP_VERSION}/github-mcp-server_Linux_x86_64.tar.gz" \
      -o /tmp/ghmcp.tar.gz \
    && tar -xzf /tmp/ghmcp.tar.gz -C /usr/local/bin github-mcp-server \
    && chmod +x /usr/local/bin/github-mcp-server \
    && rm /tmp/ghmcp.tar.gz

RUN npm install -g @anthropic-ai/claude-code @openai/codex @playwright/mcp

RUN npx -y playwright@latest install --with-deps chromium

RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /workspace /run/agent /home/agent/.claude /home/agent/.codex \
    && chown -R agent:agent /workspace /run/agent /home/agent

USER agent
WORKDIR /workspace

ENV GIT_TERMINAL_PROMPT=0 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

RUN git config --global user.email "factory@localhost" \
    && git config --global user.name "Software Factory" \
    && git config --global init.defaultBranch main \
    && git config --global --add safe.directory /workspace

CMD ["bash"]
