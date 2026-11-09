# An alternative install for homelabs; the systemd units are the other one.
FROM python:3.10-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.6.9 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN uv sync --locked --no-dev

# Everything stateful lives in /data: episodes, feeds, subscriptions, policy, Whisper model.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    DATA_DIR=/data SUBSCRIPTIONS=/data/subscriptions.opml CUT_POLICY=/data/policy.yaml \
    UPSTREAM_OPML=/data/upstream.opml HF_HOME=/data/cache/huggingface
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app:app /data
USER app
VOLUME /data
EXPOSE 5000

CMD ["gunicorn", "--workers", "2", "--threads", "4", "--timeout", "60", "--bind", "0.0.0.0:5000", "server:app"]
