#!/bin/bash
# usage: runq.sh PORT TAG model1 model2 ...
# Scores each model on the identical dataset. Run from the repository root with
# DIR=<a scratch directory holding dataset.json> (results go to $DIR/mo_<model>.json).
PORT=$1; TAG=$2; shift 2
DIR=${DIR:?set DIR to the directory holding dataset.json}
HERE=$(cd "$(dirname "$0")" && pwd)
for m in "$@"; do
  f=$(echo "$m" | sed 's#hf.co/[^/]*/##; s#[:/.]#_#g; s#-GGUF.*##' | tr 'A-Z' 'a-z')
  python "$HERE/fastbrain2.py" "$m" "$DIR/dataset.json" "$DIR/mo_$f.json" "$PORT" 2>&1 | tail -1 | tee -a "$DIR/queue_$TAG.log"
done
