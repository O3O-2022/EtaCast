"""설정 파일 · 작업 지정 · 프로필 모음.

설정은 JSON(또는 Python 3.11+에서 TOML). 예시는 README의 "설정 파일".
"""
import json
import os

from .profiles import BUILTIN, from_dict

DEFAULTS = {
    "title": "EtaCast",
    "interval": 2.0,
    "port": 8765,
    "host": "127.0.0.1",
    "jobs": [],
    "profiles": {},
    "notify": {"webhook_url": "", "format": "discord"},
}


def load_config(path=None):
    cfg = json.loads(json.dumps(DEFAULTS))  # 깊은 복사
    if not path:
        return cfg
    with open(path, encoding="utf-8") as f:
        if path.lower().endswith(".toml"):
            try:
                import tomllib
            except ImportError as e:  # 3.10 이하
                raise SystemExit("TOML 설정은 Python 3.11 이상이 필요하다 — JSON으로 쓰라") from e
            data = tomllib.loads(f.read())
        else:
            data = json.load(f)
    for k, v in data.items():
        if k == "notify" and isinstance(v, dict):
            cfg["notify"].update(v)
        else:
            cfg[k] = v
    return cfg


def profiles_from(cfg):
    prof = dict(BUILTIN)
    for name, spec in (cfg.get("profiles") or {}).items():
        spec = dict(spec)
        spec.setdefault("name", name)
        prof[name] = from_dict(spec)
    return prof


def parse_job(spec, profiles):
    """"NAME=LOG[:PROFILE]" 또는 "LOG[:PROFILE]" → (이름, 로그 경로, 프로필 이름).

    윈도 경로의 드라이브 콜론(C:\\...)과 헷갈리지 않게, 뒤의 ":이름"이 프로필일 때만 프로필로 본다.
    """
    name = None
    if "=" in spec:
        name, spec = spec.split("=", 1)
    profile = "generic"
    if ":" in spec:
        head, tail = spec.rsplit(":", 1)
        if tail in profiles and head:
            spec, profile = head, tail
    if profile not in profiles:
        raise SystemExit(f"프로필 '{profile}'이 없다 — `etacast profiles`로 목록을 본다")
    path = os.path.abspath(spec)
    return (name or os.path.splitext(os.path.basename(path))[0]), path, profile
