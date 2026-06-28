#!/bin/bash
set -e

echo "Building Docker image: neural-sync:d95ed12"
docker build -t neural-sync:d95ed12 .
echo "Build completed successfully"
