#!/bin/bash

# Print usage information
print_usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  build             Regular build using docker buildx"
    echo "  push              Push images using docker compose"
    echo ""
    exit 1
}

# Determine which Docker Compose command to use
if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE="docker-compose"
else
    echo "Error: Neither docker compose nor docker-compose command found" >&2
    exit 1
fi

# Get the command (first argument)
CMD="${1:-}"

# Check if command is empty
if [ -z "$CMD" ]; then
    echo "Error: No command specified" >&2
    print_usage
fi

shift || true

# Handle different commands
case "$CMD" in
    "build")
        # Use docker buildx bake for multi-arch build
        docker buildx bake
        ;;
    "push")
        # Use docker compose push
        $COMPOSE -f docker-compose.yml push
        ;;
    *)
        # Invalid command
        echo "Error: Invalid command '$CMD'" >&2
        print_usage
        ;;
esac
