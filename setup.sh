#!/bin/sh
# First-run setup inside Docker, so nothing has to be installed on the host.
#
#   ./setup.sh
#
# Builds the image, then runs the interactive wizard with this directory
# mounted so it can write .env (and make store/) as *you*, not as root.
set -eu
cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || {
    echo "Docker isn't installed. Without it: pip install -r requirements.txt && python3 -m tools.setup" >&2
    exit 1
}

mkdir -p store
docker build -q -t guildhall-setup . >/dev/null
exec docker run --rm -it \
    --user "$(id -u):$(id -g)" \
    -v "$PWD:/work" \
    guildhall-setup \
    python3 -m tools.setup --env-file /work/.env
