#!/bin/bash
# Lambda Layer を pip install + zip の簡易法でビルドする
# 前提: Lambdaランタイムは python3.12 (x86_64) を想定
set -e
cd "$(dirname "$0")"

rm -rf python layer.zip
mkdir -p python

python -m pip install -r requirements.txt -t python/ --platform manylinux2014_x86_64 \
  --only-binary=:all: --python-version 3.12

zip -r layer.zip python -x "*.pyc" > /dev/null
echo "✅ layer.zip を作成しました ($(du -h layer.zip | cut -f1))"
echo "次のコマンドでAWSにアップロードします:"
echo "  aws lambda publish-layer-version --layer-name stock-report-deps --zip-file fileb://layer.zip --compatible-runtimes python3.12"
