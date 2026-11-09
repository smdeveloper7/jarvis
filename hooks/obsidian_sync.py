#!/usr/bin/env python3
"""Stop 훅(JARVIS 창): JARVIS State 가 바뀌었으면 Obsidian 현황 노트를 다시 만든다 (docs/01 5절).

손으로 동기화하지 않게, JARVIS 가 답을 마칠 때마다 state·registry 의 수정 시각을 노트와 비교해
더 새로우면 scripts/obsidian_export.py 와 같은 노트(wiki/jarvis/현황.md·결정.md)를 쓴다.
바뀐 것이 없으면 아무것도 하지 않는다. 실패해도 세션을 막지 않는다.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis_state import current_role, jarvis_dir, read_input, stdio_utf8  # noqa: E402

WATCH = ('registry.yaml', 'state/tasks/*.md', 'state/decisions/*.md', 'state/releases.json', 'state/occupancy.json')


def newest(jdir):
    ts = [os.path.getmtime(p) for pat in WATCH for p in glob.glob(os.path.join(jdir, pat))]
    return max(ts or [0])


def main():
    stdio_utf8()
    inp = read_input()
    jdir = jarvis_dir()
    if not jdir:
        return 0
    _, role, _, _, _ = current_role(inp, jdir)
    if role not in ('jarvis', 'jarvis_other'):
        return 0
    os.environ['JARVIS_DIR'] = jdir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
    import obsidian_export as ox
    out_dir = os.path.join(ox.WORKLOG, 'wiki', 'jarvis')
    notes = [os.path.join(out_dir, n) for n in ('현황.md', '결정.md')]
    if all(os.path.exists(n) for n in notes) and min(os.path.getmtime(n) for n in notes) >= newest(jdir):
        return 0
    ox.write(notes[0], ox.status_note())
    ox.write(notes[1], ox.decisions_note())
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
