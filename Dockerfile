# Multi-stage build: compile the React frontend, then run FastAPI which serves
# both the API and the built frontend. Designed for Hugging Face Spaces (Docker),
# which runs the container on port 7860.

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
# Empty base => the app calls the API on the same origin (relative /api/...).
ENV VITE_API_BASE=""
RUN npm run build

# ---- Stage 2: backend + ffmpeg ----
FROM python:3.11-slim
# ffmpeg is required by pydub to decode webm/mp3/m4a/ogg audio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
# Drop the built frontend where main.py serves it from (./static).
COPY --from=frontend /app/frontend/dist ./static

ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
