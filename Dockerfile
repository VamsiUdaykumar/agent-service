# --- build stage: resolve deps into a wheel-friendly venv ---
FROM python:3.12-slim AS build

WORKDIR /build

COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install .

# --- runtime stage: slim, no build toolchain ---
FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

# Pre-create /data owned by appuser: Docker initializes a fresh named
# volume's contents from whatever already exists at the mount point in the
# image, so this is what makes the SQLite file writable by the non-root
# user the container actually runs as (compose mounts db-data here).
RUN mkdir -p /data && chown appuser:appuser /data

COPY --from=build /venv /venv
COPY app ./app

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
