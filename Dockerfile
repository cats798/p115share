# Build Stage for Frontend
FROM node:18-alpine as build-stage
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Production Stage
FROM python:3.11-slim
WORKDIR /app

# Install dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/app ./app
COPY Helper /app/Helper

# Install p115client from local source
RUN pip install -e /app/Helper/p115client-main

# Copy frontend build to static folder
COPY --from=build-stage /app/frontend/dist /app/static

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Start command
CMD ["python", "-m", "app.main"]
