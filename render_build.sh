#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Install Node.js (needed for yt-dlp to decrypt signatures)
echo "Installing Node.js..."
mkdir -p node
curl -L https://nodejs.org/dist/v18.16.0/node-v18.16.0-linux-x64.tar.xz | tar xJ -C node --strip-components=1
export PATH="$PWD/node/bin:$PATH"
node --version
