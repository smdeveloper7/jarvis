# JARVIS

한 창구에서 대화하면 프로젝트별 Coordinator 와 Worker 세션에 일을 맡기는 개인 Manager 에이전트입니다. 세션 실행과 화면은 [Paseo](https://paseo.sh) 가, 각 세션은 Claude Code(또는 Codex)가 맡습니다. 이 저장소는 그 위에 얹는 규칙과 공통 스킬입니다.

```
사용자 (Paseo 앱: PC·휴대폰)
   │ 대화
   ▼
JARVIS  ── 이 폴더에서 뜬 세션. 어디에 맡길지 정하고, 질문을 처리하고, 보고를 요약하고, 기록
   │ [배정]                         ▲ [보고] [질문] [승인요청]
   ▼                                │
프로젝트 Coordinator (프로젝트당 하나, 그 프로젝트 폴더에서)
   │ Worker 띄우기·지시               ▲ [완료] [질문]
   ▼                                │
Worker (작업당 하나, 끝나면 닫음)
```

## 역할

| 누구 | 하는 일 | 하지 않는 일 |
|---|---|---|
| 사용자 | JARVIS 와 대화, 판단, 권한 승인 중 "항상 사용자에게" 항목 | Coordinator·Worker 에 직접 지시 |
| JARVIS | 프로젝트 고르기, Coordinator 에 넘기기·없으면 만들기, 기준(`criteria.md`)으로 판단, 보고 요약, 기록 | 코드 분석, 일 쪼개기, 직접 검증 |
| Coordinator | 일 나누기, Worker 띄우기, 동시·차례 정하기, 결과 확인, JARVIS 에 `[보고]` | 다른 프로젝트 건드리기, 권한 승인, 기록 |
| Worker | 맡은 일 하나, `[완료]` 로 끝맺기 | 범위 밖 수정, push·운영 반영·DB 변경 |

규칙 원문은 [CLAUDE.md](CLAUDE.md)(JARVIS), [rules/coordinator.md](rules/coordinator.md), [rules/worker.md](rules/worker.md) 입니다.

## 파일

| 파일 | 내용 |
|---|---|
| `CLAUDE.md` | JARVIS 규칙. 이 폴더에서 뜬 세션이 읽음 |
| `rules/coordinator.md`, `rules/worker.md` | JARVIS 가 Coordinator 를 만들 때, Coordinator 가 Worker 를 만들 때 넘기는 규칙 |
| `registry.yaml` | 프로젝트 목록과 지금 Coordinator ID. 개인 상태라 git 에 올리지 않음(`registry.example.yaml` 참고) |
| `criteria.md` | JARVIS 가 사용자 대신 정할 수 있는 판단 기준. 사용자 결정에서 늘어남 |
| `.claude/settings.json` | JARVIS 권한: Paseo 도구 전체 허용 |
| `templates/project-settings.json` | 프로젝트 폴더에 둘 권한: 에이전트 만들기·지시·보관 허용, 권한 승인·강제 종료 금지 |
| `skills/git-message`, `hooks/` | 플러그인으로 설치하면 모든 세션에 걸리는 커밋·PR 문구 규칙과 검사 훅 |

## 설치

1. Paseo 데스크톱 앱을 설치하고 원격 중계는 끕니다. 설정 → 호스트 → 에이전트 → **Paseo 도구 활성화**를 켭니다.
2. 이 저장소를 `C:/Users/<나>/JARVIS` 로 받고, `registry.example.yaml` 을 `registry.yaml` 로 복사해 프로젝트를 적습니다.
3. JARVIS 폴더와 각 프로젝트 폴더를 Claude Code 로 한 번씩 열어 신뢰를 승인합니다. 신뢰하지 않은 폴더는 그 폴더의 권한 설정이 무시됩니다.
4. 프로젝트 폴더마다 `templates/project-settings.json` 을 `.claude/settings.json` 으로 둡니다.
5. 전역 `~/.claude/settings.json` 에는 Paseo 도구 중 상태·목록 보기만 허용합니다. 전역 deny 는 두지 않습니다(어디에 있든 deny 가 이겨서 JARVIS 허용까지 막음).
6. 공통 스킬·훅 플러그인:

   ```
   claude plugin marketplace add smdeveloper7/jarvis
   claude plugin install jarvis@jarvis --config python=<python 경로>
   ```

7. Paseo 에 JARVIS 폴더와 프로젝트 폴더를 프로젝트로 등록하고(`paseo project create <폴더>`), JARVIS 폴더에서 제목 `JARVIS` 로 에이전트를 띄웁니다.

## 지금 상태

- 확인함(Windows, Paseo 0.15.2, Claude Code 2.1.319): Paseo 가 띄운 Claude 세션이 CLAUDE.md·플러그인·스킬·MCP 를 그대로 읽음, git 이 아닌 폴더도 프로젝트로 등록, JARVIS → Coordinator → Worker 가 승인 창 없이 이어짐, 부모·자식 관계 기록.
- 고침: Coordinator 가 Worker 를 기다리며 낸 이른 보고를 JARVIS 가 완료로 받던 문제. 하위 에이전트 종료 알림은 부모에게 처음 한 번만 가서, Coordinator 가 다 끝난 뒤 `[보고]` 를 JARVIS 에게 직접 보내게 바꿈.
- 남은 일: 고친 보고 흐름 재시험, JARVIS 권한 승인 범위를 훅으로 막기, Obsidian 기록, 여러 프로젝트를 한 장에 보는 화면.

## 라이선스

MIT. `skills/git-message` 와 `hooks/git-message-style.py` 는 [holiday0726/claude-hub-plugin](https://github.com/holiday0726/claude-hub-plugin)(MIT)에서 가져왔습니다.
