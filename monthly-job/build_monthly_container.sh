#!/usr/bin/env bash
set -e

# tag n build image
docker build -t arxiv-monthly-image .

# inject env for key then start container
# add -t for tty
docker run -d -t --env-file .env --name arxiv-monthly-container arxiv-monthly-image

# real time reporting
docker logs -f arxiv-monthly-container

