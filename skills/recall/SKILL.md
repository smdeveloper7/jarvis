---
name: recall
description: Use when asked about past work, past decisions or why something was decided, how a problem was solved before, or "전에 어떻게 했지", "그때 왜 그렇게 정했지", "예전에 이런 문제 있었나", "지난번 결정 찾아줘", "기억 찾아줘" - 판단 기준, 위키(결정·해결법·개요), 작업일지 순으로 찾아 출처와 함께 답한다. JARVIS·PM·Worker 모두 쓴다.
---

# 기억 찾기

과거의 결정·해결법·작업을 찾아 **출처와 함께** 답한다. 기억 구조(JARVIS `docs/04`)의 4단계 지식과 3단계 일지, 그리고 규칙 층의 판단 기준을 찾는다. 추측으로 답하지 않는다.

## 찾는 순서

1. 지금 진행 중인 일이면 상태부터: `JARVIS/state/tasks/W-*.md`, `state/decisions/D-*.md`, `state/handoff/<프로젝트>/latest.md`
2. 과거 지식은 스크립트로 한 번에 찾는다(판단 기준 → 위키 결정 → 해결법 → 개요 → 그 밖의 위키 → 일지 최근 순).

   ```
   python C:/Users/SMDEVELOPER/JARVIS/scripts/recall.py <키워드…> [--project nipa] [--since 2026-10-01] [--limit 8]
   ```

   - 키워드는 2~3개, 고유한 낱말(테이블·플로우·W/D 번호·도구 이름)을 고른다. 결과가 많으면 `--project`·`--since` 로 좁힌다.
   - `[결정, 일부]` 처럼 "일부" 는 키워드 일부만 맞은 것이다. 낱말을 바꿔 다시 찾는다.
3. 걸린 노트·일지 중 답에 필요한 것만 열어 읽는다. 일지에 `refs:` 나 W·D 번호가 있으면 그 상태 파일에서 요청·보고·이유를 확인한다.
4. 여기까지 없으면 대화 원문을 찾는다: `~/.claude/projects/<작업폴더>/*.jsonl` 을 Grep(Claude 원문은 30일 뒤 지워진다). Codex 는 `~/.codex/sessions/`.

## 답하는 형식

- 결론 한두 줄, 그다음 근거와 출처 링크(`[[wiki/nipa/decisions/…]]`, `[[raw/2026-11-01]]`, `W-…`, `D-…`).
- 위키와 일지·상태 파일이 다르면 나중 것과 상태 파일을 믿고, 다르다는 사실도 적는다.
- 못 찾았으면 "기록에서 찾지 못함" 이라고 하고 어디까지 찾았는지 적는다. 기억으로 메우지 않는다.
- 찾은 내용이 앞으로도 자주 필요해 보이면 JARVIS 에게(또는 JARVIS 라면 사용자에게) 위키 노트로 정리할지 제안한다. 규칙 파일은 고치지 않는다.
