---
name: session-end
description: Use when the user says "세션 종료", "세션종료", "창 닫자", "마무리하고 닫아", or asks to end/close this JARVIS window - 영구 지식(작업일지·인계 노트·기준)을 남기고 이 JARVIS 창을 닫는다. JARVIS 창에서만 쓴다.
---

# 세션 종료

영구 지식은 사용자가 "세션 종료" 라고 한 창만 남긴다(JARVIS `docs/04` 6절, `CLAUDE.md` "세션 종료"). 보관 버튼은 세션을 즉시 멈추므로 정리는 닫기 전에 한다. 순서대로 한다.

1. **W 점검**: 이 창이 맡은 W(`state/tasks`, `jarvis:` 가 이 창)를 본다.
   - 끝난 W: `python scripts/jarvisctl.py task <W> --status done --worklog --result "…" --check "…" --next "…" [--reason "…"] [--decision D-…]`
   - 이어 갈 W: 그대로 두고 인계 노트에 다음 행동을 적는다. 남은 일이 새 일이면 `new-task` 로 W 를 연다.
   - W 없이 끝낸 의미 있는 일이 있으면 `worklog.ps1 -Project JARVIS` 로 한 건씩.
   - 정한 것과 이유, 사용자가 "기억해" 라고 한 것은 일지에 한 줄(`-Decision D-…`). 미룬 결정은 `jarvisctl decide D-… --status waiting`.
   - Linear 이슈가 있으면 `linear.py comment` 로 한 줄. 노션 할일은 진행분만 체크.
2. **인계 노트**: 열린 일만, 60줄 이하. `docs/01` 8.4 Manager 형식(프로젝트별 현황 / 사용자와 진행 중인 대화 / 운영 반영 대기 / 최근 정한 것 / 주의). 지금 `state/handoff/manager/latest.md` 의 유효한 항목은 옮겨 적는다. 임시 파일에 써서 `python scripts/jarvisctl.py handoff manager <파일>`. `stale`·`dropped_open` 이 나오면 고쳐서 다시 둔다.
3. **기준**: 사용자가 기준으로 남기자고 한 것 중 `criteria.md` 에 없는 것을 더한다.
4. **알림**: 남긴 것을 사용자에게 세 줄로 알린다.
5. **닫기**: 이 창이 기본 수신자(`registry.yaml` 의 `jarvis:`)가 아니면 `archive_agent` 로 자기 보관. 기본 수신자면 훅이 막으므로 `python scripts/jarvisctl.py transfer-jarvis --to next` 를 하고 "Paseo 에서 이 창의 보관을 누르세요" 라고 안내한다(다음 창이 `claim-jarvis` 한다).
