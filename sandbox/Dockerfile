FROM opensandbox/code-interpreter:v1.1.0

USER root

ENV PIP_INDEX_URL=https://packagefeedproxy.microsoft.io/pypi/simple \
    COPILOT_HOME=/workspace/.copilot \
    PYTHONPATH=/opt/mcd \
    PATH=/opt/mcd-venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://gh.io/copilot-install | PREFIX=/usr/local bash \
    && python3 -m venv /opt/mcd-venv \
    && /opt/mcd-venv/bin/pip install --no-cache-dir \
      --index-url https://packagefeedproxy.microsoft.io/pypi/simple \
      "mcp>=1.13,<2" "pydantic>=2.11,<3"

COPY order_mcp /opt/mcd/order_mcp
COPY run_copilot.py /opt/mcd/run_copilot.py
COPY config/mcp-config.json /opt/mcd/mcp-config.json
COPY config/copilot-instructions.md /workspace/.github/copilot-instructions.md

RUN mkdir -p /workspace/.copilot /workspace/state \
    && chmod -R 0755 /opt/mcd /workspace

WORKDIR /workspace
CMD ["bash", "-lc", "exec sleep infinity"]
