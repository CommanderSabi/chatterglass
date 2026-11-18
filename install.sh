#!/usr/bin/env bash
# Chatterglass installer — run this file.
#
# It starts the program, which asks whether to install. Nothing is written until
# you press Install in that window. Afterwards, launch Chatterglass from your
# application menu.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
exec python3 ./chatterglass.pyz "$@"
