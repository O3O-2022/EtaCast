"""Claude용 MCP 서버 (stdio, 표준 라이브러리만).

연결: claude mcp add etacast -- python -m etacast.mcp
도구: etacast_status · etacast_tail · etacast_start · etacast_wait

stdout은 프로토콜 전용이다 — 여기서 print하지 않는다(로그는 stderr).
"""
import json
import os
import subprocess
import sys
import threading
import time

from . import __version__
from .config import load_config, profiles_from
from .engine import Engine, Job, read_text

PROTOCOL = "2024-11-05"
TOOLS = [
    {"name": "etacast_status", "description": "모든 작업의 진행률 · 남은 시간 · 단계 · 오류 줄을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "etacast_tail", "description": "작업 로그의 마지막 줄들을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {"job": {"type": "string"},
                                                       "lines": {"type": "integer", "default": 40}},
                     "required": ["job"]}},
    {"name": "etacast_start", "description": "셸 명령을 백그라운드로 시작하고 진행을 지켜본다. 작업 이름을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {
         "command": {"type": "string"}, "name": {"type": "string"},
         "profile": {"type": "string", "default": "generic"}, "cwd": {"type": "string"}},
         "required": ["command"]}},
    {"name": "etacast_wait", "description": "작업이 끝나거나 실패할 때까지(또는 제한 시간까지) 기다린다.",
     "inputSchema": {"type": "object", "properties": {"job": {"type": "string"},
                                                       "timeout_sec": {"type": "number", "default": 600}},
                     "required": ["job"]}},
]


class Server:
    def __init__(self, cfg, profiles):
        self.profiles = profiles
        self.engine = Engine(interval=1.0, notify=None)
        self.logdir = os.path.join(os.path.expanduser("~"), ".etacast", "logs")
        self.count = 0
        threading.Thread(target=self.engine.loop, daemon=True).start()

    def call(self, name, args):
        if name == "etacast_status":
            return self.engine.status()
        if name == "etacast_tail":
            job = self._job(args["job"])
            lines = read_text(job.path).splitlines()
            return {"job": job.name, "log": job.path, "lines": lines[-int(args.get("lines", 40)):]}
        if name == "etacast_start":
            return self._start(args)
        if name == "etacast_wait":
            return self._wait(args["job"], float(args.get("timeout_sec", 600)))
        raise KeyError(f"모르는 도구: {name}")

    def _job(self, name):
        job = self.engine.jobs.get(name)
        if job is None:
            raise KeyError(f"작업 '{name}'이 없다 — etacast_status로 목록을 본다")
        return job

    def _start(self, args):
        self.count += 1
        os.makedirs(self.logdir, exist_ok=True)
        name = args.get("name") or f"job{self.count}-{time.strftime('%H%M%S')}"
        prof = self.profiles.get(args.get("profile", "generic"))
        if prof is None:
            raise KeyError(f"프로필 '{args.get('profile')}'이 없다")
        path = os.path.join(self.logdir, f"{name}.log")
        logf = open(path, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(args["command"], shell=True, cwd=args.get("cwd") or None,
                                stdout=logf, stderr=subprocess.STDOUT)
        self.engine.add_job(Job(name, prof, path))

        def waiter():
            rc = proc.wait()
            logf.close()
            self.engine.set_exit(name, rc)
        threading.Thread(target=waiter, daemon=True).start()
        return {"job": name, "log": path, "pid": proc.pid}

    def _wait(self, name, timeout):
        job = self._job(name)
        end = time.time() + timeout
        while time.time() < end:
            snap = job.snapshot or {}
            if snap.get("status") in ("done", "fail"):
                return snap
            time.sleep(1.0)
        return dict(job.snapshot or {}, timed_out=True)


def _reply(msg_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(server, msg):
    method = msg.get("method")
    mid = msg.get("id")
    if mid is None:  # 알림(notifications/initialized 등) — 답하지 않는다
        return
    if method == "initialize":
        return _reply(mid, {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "etacast", "version": __version__}})
    if method == "tools/list":
        return _reply(mid, {"tools": TOOLS})
    if method == "tools/call":
        p = msg.get("params", {})
        try:
            data = server.call(p.get("name"), p.get("arguments") or {})
            text = json.dumps(data, ensure_ascii=False, indent=1)
            return _reply(mid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:  # 도구 오류는 프로토콜 오류가 아니라 결과의 isError로 알린다
            return _reply(mid, {"content": [{"type": "text", "text": f"오류: {e}"}], "isError": True})
    return _reply(mid, error={"code": -32601, "message": f"모르는 메서드: {method}"})


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="etacast.mcp")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    server = Server(cfg, profiles_from(cfg))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        handle(server, msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
