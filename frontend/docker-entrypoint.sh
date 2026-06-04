#!/bin/sh
set -eu

# Replace environment variables in nginx config.
envsubst '${BACKEND_URL}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# Start nginx.
exec nginx -g "daemon off;"
