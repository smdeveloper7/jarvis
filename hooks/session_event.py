#!/usr/bin/env python3
"""SessionEnd·PreCompact 훅(PM): state/sessions.jsonl 에 기록만 남긴다 (docs/02 7절).

강제 종료·archive 때는 불리지 않을 수 있으므로 판단 근거로 쓰지 않는다.
PreCompact 는 PM 이 자동 압축까지 갔다는 뜻(교체 기준 20만을 크게 넘음)이라 경고로 남긴다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis_state import (append_jsonl, context_tokens, current_role, jarvis_dir, now_iso, read_input,  # noqa: E402
                          state, stdio_utf8)


def main():
    stdio_utf8()
    inp = read_input()
    jdir = jarvis_dir()
    if not jdir:
        return 0
    agent_id, role, proj, _, _ = current_role(inp, jdir)
    if role != 'pm':
        return 0
    ev = inp.get('hook_event_name') or ''
    row = {'timestamp': now_iso(), 'event': 'session_end' if ev == 'SessionEnd' else 'pre_compact',
           'project_id': proj, 'agent_id': agent_id, 'source': 'hook:session_event',
           'claude_session_id': inp.get('session_id')}
    if ev == 'SessionEnd':
        row['reason'] = inp.get('reason')
    else:
        row['trigger'] = inp.get('trigger')
        row['context_tokens'] = context_tokens(inp.get('transcript_path') or '')
    append_jsonl(state(jdir, 'sessions.jsonl'), row)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
