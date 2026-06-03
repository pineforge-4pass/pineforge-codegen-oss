# ---------- Stage 1: cythonize codegen to native .so ----------
FROM python:3.13 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Cython needs a C toolchain; python:3.13 (Bookworm) ships gcc.
RUN pip install "Cython==3.0.*" setuptools wheel

# Build context = repo root (see .github/workflows/release.yml).
COPY pineforge_codegen ./pineforge_codegen

# Compile every .py to native .so, then strip .py + .c. Python recognizes
# __init__.<ext>.so as a valid package marker.
RUN python <<'PY'
import glob, os, shutil, sys
from setuptools import setup
from Cython.Build import cythonize

files = sorted(glob.glob("pineforge_codegen/**/*.py", recursive=True))
print(f"cythonizing {len(files)} files")
sys.argv = ["setup.py", "build_ext", "--inplace", "-j", "4"]
setup(ext_modules=cythonize(files, language_level=3, nthreads=4))

removed = 0
for pat in ("pineforge_codegen/**/*.py", "pineforge_codegen/**/*.c"):
    for p in glob.glob(pat, recursive=True):
        os.remove(p); removed += 1
shutil.rmtree("build", ignore_errors=True)
print(f"removed {removed} source files")

import importlib.util
assert importlib.util.find_spec("pineforge_codegen"), "package broken after compile"
print("import OK")
PY


# ---------- Stage 2: runtime ----------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip install "fastapi==0.115.*" "uvicorn[standard]==0.32.*"

# Compiled package only — no .py, no .c, no Cython runtime.
COPY --from=builder /build/pineforge_codegen ./pineforge_codegen
# server.py exposes /transpile and /healthz only.
COPY docker/server.py ./server.py
# Single source of truth for version (read at startup by server.py).
COPY VERSION ./VERSION

# Smoke test: build fails here (not at first request) if Cython broke
# something at import or runtime.
RUN python -c "from pineforge_codegen import transpile; \
out = transpile('//@version=6\nstrategy(\"smoke\")\n'); \
assert '#include' in out, out[:200]; \
print('codegen smoke test passed')"

EXPOSE 8080

# UVICORN_WORKERS = processes (Python GIL), not threads. Size to the host's
# vCPU allocation; oversubscribing thrashes. Override at runtime.
ARG UVICORN_WORKERS=1
ENV UVICORN_WORKERS=${UVICORN_WORKERS}
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS}"]
