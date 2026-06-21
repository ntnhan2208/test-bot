# Use a lighter playwright base image
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Memory optimization environment variables
ENV MALLOC_ARENA_MAX=2
ENV NODE_OPTIONS="--max-old-space-size=128"

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only chromium (skip firefox/webkit to save ~400MB)
RUN playwright install chromium --with-deps

# Copy the rest of the application files
COPY . .

# Create data directory for persistent SQLite database
RUN mkdir -p /app/data

# Expose the default port (Railway will override this with the $PORT env variable)
EXPOSE 5001

# Command to run the application
CMD ["python", "app.py"]
