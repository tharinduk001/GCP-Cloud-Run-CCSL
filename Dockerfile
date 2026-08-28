# syntax=docker/dockerfile:1.7

# =========================================================
# Stage 1: "builder" - installs dependencies as plain files.
# Uses python:3.11-slim to match distroless/python3-debian12's
# bundled Python 3.11 exactly (avoids interpreter/ABI mismatch).
# This stage is THROWN AWAY at the end - none of pip, apt, or
# build tooling ends up in the final image.
# =========================================================
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Copy only the dependency manifest first -> maximizes Docker layer cache
COPY requirements.txt .

# Install dependencies as plain files into /deps (NOT a venv).
# Why: a venv's python3 binary is a symlink to the builder image's
# interpreter path, which does not exist in the distroless final
# image -> "No module named X" at runtime. Installing with --target
# avoids shipping an interpreter at all; the distroless image's own
# built-in Python 3.11 runs these files directly via PYTHONPATH.
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

# =========================================================
# Stage 2: final runtime image - "distroless"
# No shell, no package manager, no OS utilities -> drastically
# reduced attack surface. Runs as non-root (UID 65532) via the
# ":nonroot" tag.
# =========================================================
FROM gcr.io/distroless/python3-debian12:nonroot AS runtime

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/deps

WORKDIR /app

# Bring in only the installed packages (no interpreter, no build tools)
COPY --from=builder /deps /deps

# Copy application source
COPY --chown=nonroot:nonroot . /app/

USER nonroot

EXPOSE 8080

# Distroless has no shell, so ENTRYPOINT must use exec (JSON) form.
# "python3" here resolves to the distroless image's OWN interpreter -
# not anything we copied in - which is what makes this reliable.
# gunicorn.app.wsgiapp is gunicorn's own documented module-invocation
# entry point (see gunicorn.org/custom) - more reliable across versions
# than the "python -m gunicorn" shorthand.
ENTRYPOINT ["python3", "-m", "gunicorn.app.wsgiapp", "--bind", "0.0.0.0:8080", \
            "--workers", "2", "--threads", "4", "--timeout", "0", \
            "main:app"]