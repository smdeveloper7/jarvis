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
MSG_OTHER = ('이 세션은 대표 JARVIS(registry jarvis:)가 아니라 JARVIS 폴더를 읽기만 한다. '
             '대표가 되려면 python scripts/jarvisctl.py claim-jarvis <이 세션 ID> (docs/02 8절).')


def blocked(inp, jarvis, project='', nt=NT):
    """inp 는 훅 입력, project 는 CLAUDE_PROJECT_DIR(없으면 입력의 cwd)."""
    ti = inp.get('tool_input') or {}
    f = ti.get('file_path') or ti.get('notebook_path')
    if not jarvis or not f:
        return False
    root = norm(jarvis, nt)
    cwd = inp.get('cwd') or ''
    return inside(norm(f, nt, cwd), root) and not inside(norm(project or cwd, nt), root)


def not_representative(inp, jarvis, agent_id, rep, schedule=False, nt=NT):
    """JARVIS 폴더에서 뜬 Paseo 세션인데 registry 의 대표 JARVIS 가 아니면 True (docs/01 6절).
    Paseo 밖 세션(agent_id 없음), 대표가 아직 없는 경우, schedule 로 뜬 세션은 막지 않는다."""
    ti = inp.get('tool_input') or {}
    f = ti.get('file_path') or ti.get('notebook_path')
    if not jarvis or not f or not agent_id or not rep or agent_id == rep or schedule:
        return False
    root = norm(jarvis, nt)
    cwd = inp.get('cwd') or ''
    return inside(norm(f, nt, cwd), root)


def main():
    for s in (sys.stdin, sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8')
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return 0
    jarvis = os.environ.get('CLAUDE_PLUGIN_OPTION_JARVIS_DIR', '')
    if blocked(inp, jarvis, os.environ.get('CLAUDE_PROJECT_DIR', '')):
        print(MSG, file=sys.stderr)
        return 2
    agent_id = os.environ.get('PASEO_AGENT_ID', '').strip()
    if jarvis and agent_id:
        try:
            from jarvis_state import labels, paseo_record, registry
            rep = registry(jarvis).get('jarvis')
            schedule = bool(labels(paseo_record(agent_id)).get('paseo.schedule-id'))
        except Exception:
            rep, schedule = None, False
        if not_representative(inp, jarvis, agent_id, rep, schedule):
            print(MSG_OTHER, file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
