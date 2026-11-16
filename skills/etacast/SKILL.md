---
name: etacast
description: Use when a build, package, cook, export, or other long command must be watched with a progress board and time-left estimate — start it through EtaCast (MCP tools etacast_start / etacast_wait / etacast_tail / etacast_status) or wrap it with `python -m etacast run`, then report progress and the result. Also use to add a new log profile for an engine or tool.
---

# EtaCast — 긴 작업을 지켜보고 남은 시간을 알린다

## 언제 쓰나
- 빌드 · 쿠크 · 패키징처럼 몇 분 이상 걸리는 명령을 돌릴 때
- 사용자가 "끝나면 알려 줘", "얼마나 남았어?"라고 물을 때
- 로그가 쌓이는 작업에서 오류 · 경고만 모아 보고 싶을 때

## 도구 (MCP가 연결돼 있을 때)
| 도구 | 하는 일 |
| --- | --- |
| `etacast_start` | 셸 명령을 백그라운드로 시작하고 진행을 지켜본다. `name`, `profile`, `cwd` 선택. 작업 이름을 돌려준다 |
| `etacast_wait` | 끝나거나 실패할 때까지 기다린다(`timeout_sec`, 기본 600). 끝 상태와 오류 줄을 돌려준다 |
| `etacast_status` | 모든 작업의 진행률 · 남은 시간 · 단계 · 오류 줄 |
| `etacast_tail` | 작업 로그 마지막 줄들 — 실패 원인을 볼 때 |

연결: `claude mcp add etacast -- python -m etacast.mcp`

## 명령으로 (MCP 없이)
```bash
python -m etacast run --name 패키징 --profile unreal -- python build.py
python -m etacast watch --job "패키징=C:/logs/pkg.log:unreal"   # 이미 돌고 있는 로그를 지켜볼 때
python -m etacast profiles
```
`run`은 명령 출력을 그대로 보여 주고 종료 코드로 끝 · 실패를 정한다. 진행판 주소는 시작할 때 `[EtaCast] http://...`로 찍힌다.

## 절차
1. 작업을 시작한다(`etacast_start` 또는 `run`). 어떤 프로필인지 정한다 — 모르면 `generic`(퍼센트 · `[n/m]` 줄을 읽는다).
2. 사용자에게 진행판 주소와 작업 이름을 한 줄로 알린다.
3. 오래 걸리면 `etacast_wait`로 기다린다. 중간 보고는 `etacast_status`의 `frac` · `total_eta`로.
4. 실패면 `etacast_tail`로 마지막 줄을 읽고 원인을 요약한다. 추측으로 고치지 않는다.
5. 끝나면 걸린 시간과 결과를 보고한다.

## 막대가 안 움직일 때
- 막대가 0에 머물면 로그 형식이 프로필과 다르다 → `etacast profiles`로 해석기를 보고, 설정 파일의 `profiles`에 정규식을 더한다(`kind`: `percent` 또는 `ratio`).
- "재는 중"이 계속이면 처음 실행이라 기록이 없다. 한두 번 돌면 단계별 평균이 쌓여 남은 시간이 나온다(`~/.etacast/history.json`).
- 고도 · 유니티 프로필은 실물 로그로 확인하지 않았다 — 끝 표시가 안 잡히면 `run`으로 감싸 종료 코드를 쓴다.
