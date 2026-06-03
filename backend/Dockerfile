FROM python:3.12-slim

WORKDIR /app

# Install system tools used by the backend and deterministic CAD file intake.
RUN apt-get update && apt-get install -y \
    build-essential \
    cargo \
    curl \
    libreoffice \
    poppler-utils \
    rustc \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Install nodejs
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs

# Install uv
RUN pip install uv

# Configure uv to use Aliyun mirror
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy project files
COPY . .

# Set environment variables
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Start command
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
