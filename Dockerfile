# Use the official Microsoft Playwright Python base image
# This pre-installs Python, Chromium, and all system libraries required to run Playwright
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the matching chromium version for playwright
RUN playwright install chromium

# Copy the rest of the application files
COPY . .

# Expose the default port (Railway will override this with the $PORT env variable)
EXPOSE 5001

# Command to run the application
CMD ["python", "app.py"]
