#!/usr/bin/python3
"""A deterministic terminal receiver, not an LLM or a research agent."""
import json
from pathlib import Path
import select
import sys

assert Path('/.dockerenv').exists()
scene, received = map(Path, sys.argv[1:3])
previous = None
while True:
    current = scene.read_text()
    if current != previous:
        print('\033[2J\033[H' + current, flush=True)
        previous = current
    if select.select([sys.stdin], [], [], .05)[0]:
        line = sys.stdin.readline()
        if not line:
            break
        with received.open('a') as log:
            log.write(json.dumps(line.rstrip('\n'), ensure_ascii=False) + '\n')
        print('RECEIVED: ' + line.rstrip('\n'), flush=True)
        print(current, flush=True)
