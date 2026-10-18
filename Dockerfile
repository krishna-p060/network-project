# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY bproto.py bmetrics.py bserve bcurl ./

FROM base AS test
COPY SPEC.md ./
COPY www ./www
COPY tests ./tests
RUN python -m unittest discover tests

FROM base AS runtime
ARG VERSION=dev
ENV BSERVE_VERSION=${VERSION} \
    BSERVE_LOG_FORMAT=json \
    BSERVE_METRICS_PORT=9100
COPY www /srv/www
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bserve
USER 10001
EXPOSE 9000 9100
LABEL org.opencontainers.image.source="https://github.com/krishna-p060/network-project" \
      org.opencontainers.image.revision="${VERSION}"
ENTRYPOINT ["python", "/app/bserve"]
CMD ["/srv/www", "9000"]
