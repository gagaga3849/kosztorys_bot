# syntax=docker/dockerfile:1
#
# Multi-stage build for the Kosztorys Bot FastAPI app (master prompt section 5/6 + production
# hardening phase). Two stages: `builder` installs Python deps into a venv (so build tools
# like a C compiler for any wheel that needs one never end up in the final image), `runtime`
# copies just that venv + the app source and runs as a non-root user.
#
# WeasyPrint (pdf_generator.py) needs native Pango/GLib/cairo/gdk-pixbuf libraries at runtime
# on Linux - these are installed via apt-get in the runtime stage (see docs/DIARY.md's macOS
# gotcha note; on Linux there is no DYLD_FALLBACK_LIBRARY_PATH workaround needed, apt already
# puts them on the standard linker path).

FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


FROM python:3.11-slim-bookworm AS runtime

# WeasyPrint's native dependencies (Pango text shaping/rendering, cairo 2D graphics, GDK
# pixbuf image loading) + shared-mime-info (MIME type detection Weasyprint relies on) +
# a base font so PDFs don't render with missing-glyph boxes. curl is only for the
# HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf2.0-0 \
        libcairo2 \
        shared-mime-info \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/app/.cache

COPY . .
# Generated PDFs are written here by default (see config.py's OUTPUT_DIR) - owned by the
# non-root user so `save_estimate_pdf` can actually write to it. `.cache` is Fontconfig's
# (used by WeasyPrint via Pango) cache dir - without a writable one, every PDF render logs
# noisy "Fontconfig error: No writable cache directories" and re-scans fonts from scratch.
RUN mkdir -p /app/output /app/.cache && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Applies any pending Alembic migrations, then starts the app. DATABASE_URL/TELEGRAM_BOT_TOKEN
# etc. must be supplied via the container's environment (see .env.example) - config.py fails
# loudly at startup if a required one is missing.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
