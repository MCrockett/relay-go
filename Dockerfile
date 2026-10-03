# relay with everything it runs: Python 3.11, git, gh, Claude Code and Codex.
# See "Running in Docker" in README.md, and compose.example.yaml.
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 git openssh-client ca-certificates curl ripgrep \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/* \
 && python3.11 --version

ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force

# Match your host user so files written to the mounted projects keep your ownership.
ARG UID=1000
ARG GID=1000
RUN userdel -r node \
 && (getent group "${GID}" >/dev/null || groupadd -g "${GID}" relay) \
 && useradd -m -u "${UID}" -g "${GID}" -s /bin/bash relay \
 && git config --system safe.directory '*'

COPY . /opt/relay
RUN ln -s /opt/relay/bin/relay /usr/local/bin/relay

USER relay
ENV RELAY_ROOT=/projects
WORKDIR /projects
ENTRYPOINT ["/opt/relay/docker/entrypoint.sh"]
CMD ["bash"]
