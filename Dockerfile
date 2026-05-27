FROM python:3.11-slim AS build

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir hatch && hatch build -t wheel


FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/Ajay150313/slo-burn"
LABEL org.opencontainers.image.description="Multi-window SLO burn rate alerting"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app
COPY --from=build /build/dist/*.whl ./
RUN pip install --no-cache-dir *.whl && rm *.whl

VOLUME ["/etc/slo-burn"]
EXPOSE 9000 9090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')"

ENTRYPOINT ["slo-burn"]
CMD ["serve", "--config", "/etc/slo-burn/slos.yaml"]
