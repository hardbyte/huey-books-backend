#!/usr/bin/env bash

# Set bash to exit immediately on any command failure
set -e

python -m pytest -v app/tests "$@"
