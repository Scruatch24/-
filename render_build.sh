#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Install Node.js
echo "Downloading Node.js..."
mkdir -p node
curl -L https://nodejs.org/dist/v18.16.0/node-v18.16.0-linux-x64.tar.gz | tar xz -C node --strip-components=1

# Install Static FFmpeg (Fixes segfaults)
echo "Downloading Static FFmpeg..."
mkdir -p ffmpeg
curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | tar xJ -C ffmpeg --strip-components=1

echo "Verifying installations..."
export PATH="$PWD/node/bin:$PWD/ffmpeg:$PATH"
node --version
ffmpeg -version | head -n 1
echo "Build complete."
