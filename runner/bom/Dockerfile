# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
COPY --chmod=0444 requirements.lock /opt/runner/requirements.lock
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /opt/runner/requirements.lock
COPY --chmod=0444 parse.py probe.py /opt/runner/
RUN chmod 0755 /opt /opt/runner
ARG RUNNER_SOURCE_HASH
LABEL org.ontologics.runner.version="bom-parser-v1" \
      org.ontologics.runner.source-sha256="$RUNNER_SOURCE_HASH"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/python", "-I", "/opt/runner/parse.py"]
