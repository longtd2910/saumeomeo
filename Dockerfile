FROM python:3.13-slim

WORKDIR /app

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

RUN apt-get update && apt-get install -y ffmpeg nodejs npm curl && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY . /app/

CMD ["uv", "run", "python", "run.py"]