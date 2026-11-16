"""로그 해석 프로필 — 단계 목록 · 끝 · 실패 · 오류 줄을 정한다.

장르나 엔진이 바뀌면 프로필만 더하면 된다(코드를 고치지 않는다). 프로필은 세 가지로 적는다:
  * 내장: unreal · unity · godot · generic (`etacast profiles`)
  * JSON 설정 파일의 "profiles" 항목
  * 한 단계 = 시작 글 · 끝 글 · 진행을 읽는 정규식 목록(처음 맞는 것을 쓴다)
"""
import re
from dataclasses import dataclass, field

DEFAULT_DIAGNOSTICS = r"\berror\b|\bwarning\b|오류|경고"


@dataclass
class Progress:
    # percent: 그룹 1이 0~100 · ratio: 그룹 num 이 끝난 몫, 그룹 den 이 전체
    kind: str
    pattern: str
    num: int = 1
    den: int = 2
    unit: str = ""


@dataclass
class Stage:
    key: str
    name: str
    weight: float = 1.0
    start: str = ""  # 이 정규식이 처음 보이면 단계 시작. 빈 값 = 로그 처음부터
    end: str = ""    # 이 정규식이 보이면 단계 끝
    progress: list = field(default_factory=list)


@dataclass
class Profile:
    name: str
    stages: list
    finish: list = field(default_factory=list)  # 끝 표시(정규식)
    fail: list = field(default_factory=list)    # 실패 표시(정규식)
    diagnostics: str = DEFAULT_DIAGNOSTICS      # 모아 볼 오류 · 경고 줄


def _check(pattern, where):
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{where}: 정규식이 잘못됨 ({pattern!r}): {e}") from None
    return pattern


def from_dict(d):
    """JSON(dict)에서 프로필을 만든다. 잘못된 정규식이면 어디가 틀렸는지 알려 준다."""
    name = d.get("name", "custom")
    stages = []
    for s in d.get("stages", []):
        key = s["key"]
        prog = []
        for p in s.get("progress", []):
            kind = p.get("kind", "percent")
            if kind not in ("percent", "ratio"):
                raise ValueError(f"{name}/{key}: kind는 percent 또는 ratio")
            prog.append(Progress(kind, _check(p["pattern"], f"{name}/{key}"),
                                 int(p.get("num", 1)), int(p.get("den", 2)), p.get("unit", "")))
        stages.append(Stage(key, s.get("name", key), float(s.get("weight", 1.0)),
                            _check(s.get("start", ""), f"{name}/{key}.start"),
                            _check(s.get("end", ""), f"{name}/{key}.end"), prog))
    if not stages:
        raise ValueError(f"{name}: 단계가 하나도 없다")
    finish = [_check(x, f"{name}.finish") for x in d.get("finish", [])]
    fail = [_check(x, f"{name}.fail") for x in d.get("fail", [])]
    diag = _check(d.get("diagnostics", DEFAULT_DIAGNOSTICS), f"{name}.diagnostics")
    return Profile(name, stages, finish, fail, diag)


UNREAL = Profile(
    "unreal",
    stages=[
        Stage("build", "C++ 빌드", 0.25, start=r"BUILD COMMAND STARTED", end=r"BUILD COMMAND COMPLETED",
              progress=[Progress("ratio", r"(?m)^\[(\d+)/(\d+)\]", 1, 2, "파일")]),
        Stage("cook", "쿠크(에셋 굽기)", 0.40, start=r"COOK COMMAND STARTED", end=r"COOK COMMAND COMPLETED",
              progress=[Progress("ratio", r"Cooked packages (\d+) Packages Remain (\d+) Total (\d+)", 1, 3, "에셋")]),
        Stage("stage", "스테이지 · pak 묶기", 0.25, start=r"STAGE COMMAND STARTED", end=r"STAGE COMMAND COMPLETED",
              progress=[Progress("percent", r"(\d+)% complete")]),
        Stage("archive", "내보내기", 0.10, start=r"ARCHIVE COMMAND STARTED", end=r"ARCHIVE COMMAND COMPLETED"),
    ],
    finish=[r"BUILD SUCCESSFUL", r"\[pkg\] done"],
    fail=[r"BUILD FAILED", r"\[pkg\] done code=(?!0)"],
)

UNITY = Profile(  # 확인은 Editor 로그의 빌드 보고 문장에 맞춘 것 — 줄마다 %가 없으면 막대는 0에 머문다
    "unity",
    stages=[Stage("build", "빌드", 1.0, progress=[Progress("percent", r"(\d{1,3})%")])],
    finish=[r"Build completed with a result of 'Succeeded'"],
    fail=[r"Build completed with a result of 'Failed'", r"Build failed"],
)

GODOT = Profile(  # 고도 내보내기 줄 형식은 아직 실물로 확인 못 함 — 끝은 `etacast run`의 종료 코드로 잡는다
    "godot",
    stages=[Stage("export", "내보내기", 1.0, progress=[Progress("percent", r"(\d{1,3})%")])],
)

GENERIC = Profile(
    "generic",
    stages=[Stage("work", "진행", 1.0, progress=[
        Progress("ratio", r"\[(\d+)/(\d+)\]", 1, 2, "단계"),           # ninja · cmake
        Progress("ratio", r"Building \[[=>\- ]*\]\s*(\d+)/(\d+)", 1, 2, "크레이트"),  # cargo
        Progress("percent", r"(\d{1,3})%"),                             # webpack · 퍼센트 줄
    ])],
    finish=[r"BUILD SUCCESSFUL", r"Finished `(dev|release)` profile"],
    fail=[r"BUILD FAILED", r"error: could not compile"],
)

BUILTIN = {p.name: p for p in (UNREAL, UNITY, GODOT, GENERIC)}


def evaluate(profile, text):
    """로그 전체 글을 프로필로 읽는다 → 단계별 상태 · 끝/실패 · 오류 줄."""
    finished = any(re.search(p, text, re.M) for p in profile.finish)
    failed = any(re.search(p, text, re.M) for p in profile.fail)
    stages = []
    for st in profile.stages:
        sm = re.search(st.start, text, re.M) if st.start else None
        if st.start:
            started = sm is not None
            body = text[sm.start():] if started else ""
        else:
            started = bool(text.strip())
            body = text
        ended = bool(st.end) and re.search(st.end, text, re.M) is not None
        if ended or finished:
            state, frac, detail = "done", 1.0, ""
        elif started:
            state, frac, detail = "run", 0.0, ""
            for p in st.progress:
                ms = list(re.finditer(p.pattern, body, re.M))
                if not ms:
                    continue
                m = ms[-1]
                if p.kind == "percent":
                    v = int(m.group(1))
                    frac, detail = min(v, 100) / 100.0, f"{v}%"
                else:
                    a, b = int(m.group(p.num)), int(m.group(p.den))
                    frac = min(a / max(b, 1), 1.0)
                    detail = f"{a} / {b} {p.unit}".rstrip()
                break
        else:
            state, frac, detail = "wait", 0.0, ""
        stages.append({"key": st.key, "name": st.name, "weight": st.weight,
                       "state": state, "frac": frac, "detail": detail})
    diag = []
    if profile.diagnostics:
        rx = re.compile(profile.diagnostics, re.I)
        for line in text.splitlines():
            if rx.search(line):
                diag.append(line.strip()[:200])
        diag = diag[-30:]
    return {"stages": stages, "finished": finished, "failed": failed, "diagnostics": diag}
