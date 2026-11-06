#!/usr/bin/env python3
"""PreToolUse 훅: JARVIS 폴더 아래 파일은 JARVIS(작업 폴더가 JARVIS 폴더인 세션)만 Edit/Write 한다.

Coordinator·Worker 가 registry, criteria, 상태 파일, 규칙을 고치지 못하게 막는다. 바꿀 내용은 JARVIS 에게 보낸다.
JARVIS 폴더는 플러그인 설정 jarvis_dir(훅에는 CLAUDE_PLUGIN_OPTION_JARVIS_DIR)이다. 비어 있으면 아무것도 막지 않는다.
Bash 로 쓰는 것(sed -i, >)은 막지 못한다. lanekeeper(holiday0726, MIT)의 hub_only_write.py 를 바탕으로 했다.
"""
import json
import os
import sys

from pathutil import NT, inside, norm

MSG = ('JARVIS 폴더 파일은 JARVIS 만 고친다. 바꿀 내용은 JARVIS 에게 [보고]·[점유]·[해제] 로 보내라.')


def blocked(inp, jarvis, project='', nt=NT):
    """inp 는 훅 입력, project 는 CLAUDE_PROJECT_DIR(없으면 입력의 cwd)."""
    ti = inp.get('tool_input') or {}
    f = ti.get('file_path') or ti.get('notebook_path')
    if not jarvis or not f:
        return False
    root = norm(jarvis, nt)
    cwd = inp.get('cwd') or ''
    return inside(norm(f, nt, cwd), root) and not inside(norm(project or cwd, nt), root)


def main():
    for s in (sys.stdin, sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return 0
    if blocked(inp, os.environ.get('CLAUDE_PLUGIN_OPTION_JARVIS_DIR', ''), os.environ.get('CLAUDE_PROJECT_DIR', '')):
        print(MSG, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
