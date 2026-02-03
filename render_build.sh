#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Install Node.js (needed for yt-dlp to decrypt signatures)
# Using .tar.gz instead of .tar.xz for better compatibility
echo "Downloading Node.js..."
mkdir -p node
curl -L https://nodejs.org/dist/v18.16.0/node-v18.16.0-linux-x64.tar.gz | tar xz -C node --strip-components=1

echo "Verifying Node.js path..."
export PATH="$PWD/node/bin:$PATH"
node --version
echo "Node.js installation complete."
