#!/usr/bin/env python3
"""PreToolUse 훅: JARVIS 폴더 파일 쓰기 규칙 (docs/01 6절).

1. JARVIS 폴더 아래 파일은 JARVIS(작업 폴더가 JARVIS 폴더인 세션)만 Edit/Write 한다.
   PM·Worker 가 registry, criteria, 상태 파일, 규칙을 고치지 못하게 막는다. 바꿀 내용은 JARVIS 에게 보낸다.
2. JARVIS 창은 여러 개일 수 있으므로, 겹치면 안 되는 쓰기는 Paseo 세션이면 JARVIS 라도 Edit/Write 로 하지 않는다.
   - registry.yaml: scripts/jarvisctl.py(register, set, switch, claim-jarvis)로만 바꾼다(잠금 + CAS).
   - state/occupancy.json: jarvisctl occupy·vacate 로만(확인과 등록을 한 잠금 안에서).
   - 새 작업 파일(state/tasks/W-*.md 가 아직 없을 때): jarvisctl new-task 로 번호를 받아 만든다. 있는 작업 파일 고치기는 된다.
   - 결정 파일(state/decisions/D-*.md): 있는 것도 직접 고치지 않는다. new-decision·decide·decision-note 로.
   Paseo 밖에서 사용자가 직접 연 세션은 막지 않는다.

JARVIS 폴더는 플러그인 설정 jarvis_dir(훅에는 CLAUDE_PLUGIN_OPTION_JARVIS_DIR)이다. 비어 있으면 아무것도 막지 않는다.
Bash 로 쓰는 것(sed -i, >)은 막지 못한다. lanekeeper(holiday0726, MIT)의 hub_only_write.py 를 바탕으로 했다.
"""
import json
import os
import re
import sys

from pathutil import NT, inside, native, norm

MSG = ('JARVIS 폴더 파일은 JARVIS 만 고친다. 바꿀 내용은 JARVIS 에게 [보고]·[점유]·[해제] 로 보내라.')
MSG_REG = ('registry.yaml 은 JARVIS 창이 여럿이라 직접 고치지 않는다. python scripts/jarvisctl.py register·switch·'
           'claim-jarvis 로 바꾼다(잠금 안에서 CAS).')
MSG_OCC = ('공유 자원 점유는 창끼리 겹치지 않게 python scripts/jarvisctl.py occupy·vacate 로만 바꾼다.')
MSG_DEC = ('결정 파일은 창끼리 답이 엇갈리지 않게 Edit·Write 로 고치지 않는다. 확정·미루기·필요 없음은 jarvisctl decide, '
           '설명 추가는 jarvisctl decision-note, 새 결정은 new-decision.')
DEC_RE = re.compile(r'/state/decisions/D-[^/]*\.md$', re.I)
MSG_NEW = ('새 작업·결정 파일은 번호가 겹치지 않게 python scripts/jarvisctl.py new-task <프로젝트> --title … 또는 '
           'new-decision <프로젝트> --question … 으로 만든 뒤 그 파일을 고친다.')
NEW_RE = re.compile(r'/state/(tasks/W-|decisions/D-)[^/]*\.md$', re.I)   # 비교 경로는 소문자로 접힌다


def blocked(inp, jarvis, project='', nt=NT):
    """inp 는 훅 입력, project 는 CLAUDE_PROJECT_DIR(없으면 입력의 cwd)."""
    ti = inp.get('tool_input') or {}
    f = ti.get('file_path') or ti.get('notebook_path')
    if not jarvis or not f:
        return False
    root = norm(jarvis, nt)
    cwd = inp.get('cwd') or ''
    return inside(norm(f, nt, cwd), root) and not inside(norm(project or cwd, nt), root)


def shared_block(inp, jarvis, agent_id, exists=os.path.exists, nt=NT):
    """Paseo JARVIS 세션의 겹치면 안 되는 쓰기면 안내 문구, 아니면 None."""
    ti = inp.get('tool_input') or {}
    f = ti.get('file_path') or ti.get('notebook_path')
    if not jarvis or not f or not agent_id:
        return None
    root = norm(jarvis, nt)
    cwd = inp.get('cwd') or ''
    path = norm(f, nt, cwd)
    if not inside(path, root):
        return None
    rel = path[len(root):]
    if rel == '/registry.yaml':
        return MSG_REG
    if rel == '/state/occupancy.json':
        return MSG_OCC
    if DEC_RE.search(rel):
        return MSG_DEC
    if NEW_RE.search(rel) and not exists(native(f, nt) if os.path.isabs(native(f, nt)) else os.path.join(cwd, f)):
        return MSG_NEW
    return None


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
    why = shared_block(inp, jarvis, os.environ.get('PASEO_AGENT_ID', '').strip())
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
