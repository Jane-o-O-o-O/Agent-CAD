# AI Manus

English | [中文](README_zh.md)

[![GitHub stars](https://img.shields.io/github/stars/simpleyyt/ai-manus?style=social)](https://github.com/simpleyyt/ai-manus/stargazers)
&ensp;
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

AI Manus is a general-purpose AI Agent system that supports running various tools and operations in a sandbox environment.

Enjoy your own agent with AI Manus!

## Demos

### Browser Use

* Task: Latest LLM papers

<https://github.com/user-attachments/assets/4e35bc4d-024a-4617-8def-a537a94bd285>

### Code Use

* Task: Write a complex Python example

<https://github.com/user-attachments/assets/765ea387-bb1c-4dc2-b03e-716698feef77>

### Overall Design

![Image](https://github.com/user-attachments/assets/69775011-1eb7-452f-adaf-cd6603a4dde5)

**When a user initiates a conversation:**

1. Web sends a request to create an Agent to the Server, which creates a Sandbox through `/var/run/docker.sock` and returns a session ID.
2. The Sandbox is an Ubuntu Docker environment that starts Chrome browser and API services for tools like File/Shell.
3. Web sends user messages to the session ID, and when the Server receives user messages, it forwards them to the PlanAct Agent for processing.
4. During processing, the PlanAct Agent calls relevant tools to complete tasks.
5. All events generated during Agent processing are sent back to Web via SSE.

**When users browse tools:**

- Browser:
    1. The Sandbox's headless browser starts a VNC service through xvfb and x11vnc, and converts VNC to websocket through websockify.
    2. Web's NoVNC component connects to the Sandbox through the Server's Websocket Forward, enabling browser viewing.
- Other tools: Other tools work on similar principles.

## Environment Requirements

This project primarily relies on Docker for development and deployment, requiring a relatively new version of Docker:
- Docker 20.10+
- Docker Compose

Model capability requirements:
- Compatible with OpenAI interface
- Support for FunctionCall
- Support for Json Format output

Deepseek and GPT models are recommended.

## Deployment Guide

Docker Compose is recommended for deployment:

```yaml
services:
  frontend:
    image: simpleyyt/manus-frontend
    ports:
      - "5173:80"
    depends_on:
      - backend
    restart: unless-stopped
    networks:
      - manus-network
    environment:
      - BACKEND_URL=http://backend:8000

  backend:
    image: simpleyyt/manus-backend
    depends_on:
      - sandbox
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - manus-network
    environment:
      # OpenAI API base URL
      - API_BASE=https://api.openai.com/v1
      # OpenAI API key, replace with your own
      - API_KEY=sk-xxxx
      # LLM model name
      - MODEL_NAME=gpt-4o
      # LLM temperature parameter, controls randomness
      - TEMPERATURE=0.7 
      # Maximum tokens for LLM response
      - MAX_TOKENS=2000
      # Google Search API key for web search capability
      #- GOOGLE_SEARCH_API_KEY=
      # Google Custom Search Engine ID
      #- GOOGLE_SEARCH_ENGINE_ID=
      # Application log level
      - LOG_LEVEL=INFO
      # Docker image used for the sandbox
      - SANDBOX_IMAGE=simpleyyt/manus-sandbox
      # Prefix for sandbox container names
      - SANDBOX_NAME_PREFIX=sandbox
      # Time-to-live for sandbox containers in minutes
      - SANDBOX_TTL_MINUTES=30
      # Docker network for sandbox containers
      - SANDBOX_NETWORK=manus-network

  sandbox:
    image: simpleyyt/manus-sandbox
    command: /bin/sh -c "exit 0"  # prevent sandbox from starting, ensure image is pulled
    restart: "no"
    networks:
      - manus-network

networks:
  manus-network:
    name: manus-network
    driver: bridge
```

Save as `docker-compose.yml` file, and run:

```shell
docker compose up -d
```

> Note: If you see `sandbox-1 exited with code 0`, this is normal, as it ensures the sandbox image is successfully pulled locally.

Open your browser and visit <http://localhost:5173> to access Manus.

## Development Guide

### Project Structure

This project consists of three independent sub-projects:

* `frontend`: manus frontend
* `backend`: Manus backend
* `sandbox`: Manus sandbox

### Environment Setup

1. Download the project:
```bash
git clone https://github.com/simpleyyt/ai-manus.git
cd ai-manus
```

2. Copy the configuration file:
```bash
cp .env.example .env
```

3. Modify the configuration file:
```
# Model provider configuration
API_KEY=
API_BASE=https://api.openai.com/v1

# Model configuration
MODEL_NAME=gpt-4o
TEMPERATURE=0.7
MAX_TOKENS=2000

# Optional: Google search configuration
#GOOGLE_SEARCH_API_KEY=
#GOOGLE_SEARCH_ENGINE_ID=

# Sandbox configuration
SANDBOX_IMAGE=simpleyyt/manus-sandbox
SANDBOX_NAME_PREFIX=sandbox
SANDBOX_TTL_MINUTES=30
SANDBOX_NETWORK=manus-network

# Log configuration
LOG_LEVEL=INFO
```

### Development and Debugging

1. Run in debug mode:
```bash
# Equivalent to docker compose -f docker-compose-development.yaml up
./dev.sh up
```

All services will run in reload mode, and code changes will be automatically reloaded. The exposed ports are as follows:
- 5173: Web frontend port
- 8000: Server API service port
- 8080: Sandbox API service port
- 5900: Sandbox VNC port
- 9222: Sandbox Chrome browser CDP port

> *Note: In Debug mode, only one sandbox will be started globally*

2. When dependencies change (requirements.txt or package.json), clean up and rebuild:
```bash
# Clean up all related resources
./dev.sh down -v

# Rebuild images
./dev.sh build

# Run in debug mode
./dev.sh up
```

### Image Publishing

```bash
export IMAGE_REGISTRY=your-registry-url
export IMAGE_TAG=latest

# Build images
./run build

# Push to the corresponding image repository
./run push
``` 
