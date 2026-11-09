#!/usr/bin/env python3
"""UserPromptSubmit 훅(JARVIS 창): registry 의 PM 이 Paseo 에서 닫혔는지 대조한다 (docs/02 6절).

사용자가 Paseo 화면에서 PM 을 archive·delete 하면 Claude 쪽에는 이벤트가 없다. 그래서 JARVIS 가
글을 받을 때마다(PM 보고도 JARVIS 에게는 사용자 글로 들어온다) Paseo 기록을 보고, 처음 본 닫힘이면
sessions.jsonl 에 남기고 JARVIS 컨텍스트에 알린다. registry 는 바꾸지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis_state import (append_jsonl, closed_state, current_role, emit_context, jarvis_dir, now_iso,  # noqa: E402
                          open_tasks, paseo_record, read_input, read_json, state, stdio_utf8, write_json)


def reconcile(jdir, reg, home=None):
    """새로 닫힌 PM 마다 알림 한 줄. 이미 알린 것은 state/sessions/reconciled.json 으로 건너뛴다."""
    seen_path = state(jdir, 'sessions', 'reconciled.json')
    seen = read_json(seen_path, {}) or {}
    msgs, changed = [], False
    for k, p in reg['projects'].items():
        aid = p.get('coordinator')
        if not aid:
            continue
        how = closed_state(aid, home)
        if how in ('open', 'error'):       # 읽기 실패는 다음에 다시 본다(기록·표식 안 남김)
            continue
        rec = paseo_record(aid, home)
        key = '%s:%s' % (aid, how)
        if seen.get(key):
            continue
        seen[key] = now_iso()
        changed = True
        n = len(open_tasks(jdir, k))
        append_jsonl(state(jdir, 'sessions.jsonl'), {
            'timestamp': now_iso(), 'event': 'closed', 'project_id': k, 'agent_id': aid,
            'closed_by': 'unknown', 'archived_at': (rec or {}).get('archivedAt'),
            'how': how, 'source': 'reconcile'})
        what = {'archived': '닫혔다(archive)', 'missing': '사라졌다(삭제)',
                'closed': '프로세스가 닫혔다(archive 아님, 다시 열 수 있을 수 있음)'}[how]
        msgs.append('[알림] %s PM(%s) 이 Paseo 에서 %s. 열린 작업 %d건. registry 는 그대로다. '
                    '다음 [배정] 때 "PM 띄우기"(mode crash, state/handoff/%s/latest.md)로 새로 띄운다.'
                    % (k, aid[:8], what, n, k))
    if changed:
        write_json(seen_path, seen)
    return msgs


def main():
    stdio_utf8()
    inp = read_input()
    jdir = jarvis_dir()
    if not jdir:
        return 0
    _, role, _, reg, _ = current_role(inp, jdir)
    if role not in ('jarvis', 'jarvis_other'):     # 어느 JARVIS 창이든 처음 본 창이 알린다
        return 0
    msgs = reconcile(jdir, reg)
    if msgs:
        emit_context(inp.get('hook_event_name') or 'UserPromptSubmit', '\n'.join(msgs))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:  # 훅 실패로 세션을 막지 않는다
        sys.exit(0)
