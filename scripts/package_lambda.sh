#!/usr/bin/env bash
# Bundle the pokerleague package and its dependencies for the arm64 Python 3.12 Lambda.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda
mkdir -p build/lambda
uv pip install --quiet --target build/lambda \
  --python-platform aarch64-manylinux2014 --python-version 3.12 --only-binary=:all: \
  "anthropic[bedrock]>=2.8"
cp -R src/pokerleague build/lambda/
find build/lambda -name "__pycache__" -type d -prune -exec rm -rf {} +
echo "lambda bundle: $(du -sh build/lambda | cut -f1)"
