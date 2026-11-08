#!/usr/bin/env python3
"""Stop 훅(PM): 응답 한 차례가 끝날 때 컨텍스트 크기를 state/sessions/<에이전트ID>.json 에 쓴다 (docs/01 5.5).

Paseo lastUsage 는 데몬 재시작 뒤 비므로, JARVIS 는 교체 판단에 이 파일을 함께 본다(observed_at 으로 최신성 확인).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis_state import (context_tokens, current_role, jarvis_dir, now_iso, read_input, state,  # noqa: E402
                          stdio_utf8, write_json)


def main():
    stdio_utf8()
    inp = read_input()
    jdir = jarvis_dir()
    if not jdir:
        return 0
    agent_id, role, proj, _, _ = current_role(inp, jdir)
    if role != 'pm':
        return 0
    tokens = context_tokens(inp.get('transcript_path') or '')
    if tokens is None:
        return 0
    write_json(state(jdir, 'sessions', agent_id + '.json'), {
        'agent_id': agent_id, 'project_id': proj, 'role': 'pm', 'observed_at': now_iso(),
        'context_tokens': tokens, 'source': 'transcript', 'claude_session_id': inp.get('session_id')})
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
