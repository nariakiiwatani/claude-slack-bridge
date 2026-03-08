#!/bin/bash
echo "lsを実行して" | claude -p --allowedTools "Read" --output-format json 2>/dev/null
