# Use lightweight Python base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install required system packages for Playwright and Chromium
RUN apt-get update && apt-get install -y \
    wget \
    libnss3 \
    libasound2 \
    libxss1 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium)
RUN python -m playwright install chromium

# Copy your script into the container
COPY ipo_gmp_telegram.py .

# Set environment variables (for Telegram)
ENV BOT_TOKEN=""
ENV CHAT_ID=""

# Command to run your script
CMD ["python", "ipo_gmp_telegram.py"]
