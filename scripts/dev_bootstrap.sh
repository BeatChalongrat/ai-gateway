#!/usr/bin/env bash
set -e
cp .env.example .env || true
echo "Created .env if missing. Start with: docker compose up --build"
