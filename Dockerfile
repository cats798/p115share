# Build Stage for Frontend
FROM node:20-alpine AS build-stage
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Production Stage
FROM python:3.12-slim
WORKDIR /app

# Create config directory
RUN mkdir -p /app/config

# Install dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir p115client


# Copy backend code
COPY backend/app ./app

# Copy frontend build to static folder
COPY --from=build-stage /app/frontend/dist /app/static

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Start command
CMD ["python", "-m", "app.main"]
