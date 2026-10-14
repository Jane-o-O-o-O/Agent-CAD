FROM python:3.12-slim

WORKDIR /app

# Preserve the local mirror default; CI can keep the image's official apt sources.
ARG USE_ALIYUN_MIRROR=true
RUN if [ "$USE_ALIYUN_MIRROR" = "true" ]; then \
      sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources && \
      sed -i 's|http://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list.d/debian.sources; \
    fi

# Install system tools used by the backend and deterministic CAD file intake.
# OCP novtk still links OpenGL/X11 and fontconfig's system Expat.
RUN apt-get update && apt-get install -y \
    build-essential \
    cargo \
    curl \
    libexpat1 \
    libgl1 \
    libseccomp2 \
    libx11-6 \
    libreoffice \
    poppler-utils \
    rustc \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Install nodejs
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Configure uv to use Aliyun mirror
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# Catch missing CAD shared libraries before the API can report healthy.
RUN /app/.venv/bin/python -c "from build123d import Box; shape = Box(1, 2, 3); assert shape.is_valid and abs(shape.volume - 6) < 1e-9"

# Copy project files
COPY . .

# Set environment variables
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Start command
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
