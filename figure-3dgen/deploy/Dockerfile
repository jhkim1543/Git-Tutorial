# Single container: FastAPI API + built frontend under /3dgen, headless Blender via the bpy module.
# Build from the figure-3dgen/ directory:  docker build -f deploy/Dockerfile -t figure-3dgen .
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
# bpy (Blender 4.2 as a Python module) needs a few X/GL client libraries even when headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libxrender1 libxxf86vm1 libxfixes3 libxi6 libxkbcommon0 libsm6 libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt bpy==4.2.0
COPY backend backend
COPY contracts contracts
COPY reference reference
COPY --from=web /web/dist frontend/dist
ENV FIG3D_BASE_PATH=/3dgen FIG3D_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8030
WORKDIR /app/backend
# OPENAI_API_KEY (Astra) is injected at runtime from the server secret store — never baked into the image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030", "--workers", "1"]
