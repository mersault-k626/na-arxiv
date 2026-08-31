#!/usr/bin/env bash
set -e

# tag n build image
docker build -t arxiv-backfill-image .

# inject env for key then start container
# add -t for tty
docker run -d -t --env-file .env --name arxiv-backfill-container arxiv-backfill-image

# real time reporting
docker logs -f arxiv-backfill-container
