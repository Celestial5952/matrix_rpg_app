# Guildhall bot. Runtime only — tests run on the host, not in here.
FROM python:3.12-slim

# No build toolchain needed: matrix-nio ships wheels, and core/ is pure stdlib.
WORKDIR /app

COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY core/ core/
COPY adapters/ adapters/
COPY tools/ tools/

# State MUST live on a mounted volume, never in the image layer. A container
# without one is a fresh cold start on every restart: every character wiped,
# and the sync token lost. Losing the sync token is what made the old
# clock-skew bug fire every single time instead of just once.
ENV MATRIX_STATE_DIR=/data
RUN mkdir -p /data && useradd --create-home --uid 10001 guildhall \
    && chown guildhall:guildhall /data
VOLUME ["/data"]

# The bot needs no privileges of any kind.
USER guildhall

# Unbuffered, or logs sit in a pipe buffer and `docker logs` looks empty
# during exactly the incident you're trying to debug.
ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "adapters.matrix"]
