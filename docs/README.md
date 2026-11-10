# JARVIS 관리정책 안내

JARVIS는 요청을 작업으로 나누어 PM에게 맡기고, 세션이 바뀌어도 기록을 바탕으로 일을 이어 간다. 네 문서는 이 과정의 서로 다른 질문에 답한다.

| 읽을 문서 | 핵심 질문 | 기억할 개념 |
|---|---|---|
| [01. 상태·기억 관리](01_State_Memory_Management.md) | 무엇을 남겨야 일을 이어 갈 수 있는가? | 상태는 현재 사실, 인계는 다음 행동, 기억은 재사용할 지식 |
| [02. 에이전트 관리](02_Agent_Lifecycle_Management.md) | 담당 세션을 어떻게 안전하게 바꾸는가? | 역할은 유지하고 검증한 세션으로 담당을 전환 |
| [03. 업무 관리](03_Work_Lifecycle_Management.md) | 어떤 단위로 맡기고 언제 완료하는가? | 기능·개발 작업·위임의 완료를 각각 판단 |
| [04. 기억 구조](04_Memory_Structure.md) | 창이 닫혀도 무엇이 어디에 남고, 다음 세션은 무엇을 읽는가? | 원문 → 상태 → 일지 → 지식 네 단계와 따로 두는 규칙 층 |

## 문서 읽는 방법

01~03 은 개념·정책·명령어·파일 형식·훅·예외를 한 문서에서 확인할 수 있게 썼다. 04 는 기억 흐름의 원본이고, 01 의 기억 관련 절은 04 로 연결한다. 구현·검증 현황은 2026-11-09 기준이다.

`reference/` 는 정책이 아닌 참고 자료다. [Paseo 는 어떻게 돌아가나](reference/paseo_internals.md)(데몬, MCP, CLI 실행 원리)와 [lanekeeper 와 JARVIS 비교](reference/lanekeeper_comparison.md) 가 있다. 세션 실행 바탕(Paseo)은 02 2절, 에이전트 사이 메시지 규약은 03 4절에 있다.

## 그림

문서 안 순서도는 mermaid 로 본문에 둔다. 구성도처럼 손으로 배치한 그림은 `diagrams/*.html` 에 SVG 로 그리고, `images/` 에 밝은 판과 어두운 판 PNG 를 둔다.

| 원본 | PNG | 쓰는 곳 |
|---|---|---|
| `diagrams/layers.html` | `images/layers-*.png` | README 세 계층 |
| `diagrams/architecture.html` | `images/architecture-*.png` | README 구성 요소 |
| `diagrams/flow.html` | `images/flow-*.png` | README 배정에서 보고까지 |
| `diagrams/state-storage.html` | `images/state-storage-*.png` | 01 2절 전체 저장 구조 |
| `diagrams/state-memory.html` | `images/state-memory-*.png` | 01 5절, README 기억 흐름 |

색과 글꼴은 `diagrams/diagram.css` 하나에 있고, 어두운 판은 `prefers-color-scheme: dark` 로 바뀐다. HTML 을 고친 뒤에는 Chrome 헤드리스로 2배 크기 PNG 를 다시 만든다(높이는 SVG `viewBox` 의 높이).

```powershell
$ch = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$u  = "file:///C:/Users/<나>/JARVIS/docs/diagrams/state-memory.html"
& $ch --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1100,400 --screenshot=state-memory-light.png $u
& $ch --headless=new --hide-scrollbars --force-dark-mode --force-device-scale-factor=2 --window-size=1100,400 --screenshot=state-memory-dark.png $u
```
