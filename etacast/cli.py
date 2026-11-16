"""명령줄 — `etacast watch` · `etacast run` · `etacast profiles`.

  etacast watch --job build=C:\\logs\\build.log:unreal --port 8765
  etacast run --name 패키징 -- python build.py
  etacast profiles
"""
import argparse
import http.server
import os
import subprocess
import sys
import threading
import time

from . import __version__
from .config import load_config, parse_job, profiles_from
from .engine import Engine, Job, send_webhook
from .web import make_handler


def _notifier(cfg):
    hook = (cfg.get("notify") or {}).get("webhook_url")
    if not hook:
        return None
    fmt = cfg["notify"].get("format", "discord")

    def send(name, status, text):
        threading.Thread(target=send_webhook, args=(hook, fmt, text), daemon=True).start()
    return send


def _serve(engine, host, port, title):
    srv = http.server.ThreadingHTTPServer((host, port), make_handler(engine, title))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _common(p):
    p.add_argument("--config", help="설정 파일(JSON 또는 TOML)")
    p.add_argument("--port", type=int)
    p.add_argument("--host")
    p.add_argument("--interval", type=float, help="갱신 주기(초) — 0.5 · 1 · 2 · 5 · 10 · 아무 양수")
    p.add_argument("--title")


def cmd_watch(args, cfg, profiles):
    engine = Engine(args.interval or cfg["interval"], notify=_notifier(cfg))
    for spec in args.job:
        name, path, prof = parse_job(spec, profiles)
        engine.add_job(Job(name, profiles[prof], path))
    for j in cfg.get("jobs", []):
        spec = f'{j["name"]}={j["log"]}' + (f':{j["profile"]}' if j.get("profile") else "")
        name, path, prof = parse_job(spec, profiles)
        engine.add_job(Job(name, profiles[prof], path))
    threading.Thread(target=engine.loop, daemon=True).start()
    host, port = args.host or cfg["host"], args.port or cfg["port"]
    title = args.title or cfg["title"]
    srv = _serve(engine, host, port, title)
    print(f"[EtaCast] {title}: http://{host}:{port}  (작업 {len(engine.jobs)}개) — 멈추려면 Ctrl+C", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


def cmd_run(args, cfg, profiles):
    if not args.cmd:
        raise SystemExit("감쌀 명령을 `--` 뒤에 적는다: etacast run -- <명령>")
    cmd = args.cmd[1:] if args.cmd[0] == "--" else args.cmd
    name = args.name or os.path.basename(cmd[0])
    logdir = os.path.join(os.path.expanduser("~"), ".etacast", "logs")
    os.makedirs(logdir, exist_ok=True)
    path = os.path.join(logdir, f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log")
    engine = Engine(args.interval or cfg["interval"], notify=_notifier(cfg))
    engine.add_job(Job(name, profiles[args.profile], path))
    threading.Thread(target=engine.loop, daemon=True).start()
    host, port = args.host or cfg["host"], args.port or cfg["port"]
    try:
        _serve(engine, host, port, args.title or cfg["title"])
        print(f"[EtaCast] http://{host}:{port}", flush=True)
    except OSError as e:
        print(f"[EtaCast] 진행판 포트를 못 열었다({e}) — 명령은 그대로 돈다", flush=True)
    print(f"[EtaCast] 로그: {path}", flush=True)
    with open(path, "w", encoding=args.encoding, errors="replace") as logf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for raw in proc.stdout:
            line = raw.decode(args.encoding, errors="replace")
            sys.stdout.write(line)
            sys.stdout.flush()
            logf.write(line)
            logf.flush()
        rc = proc.wait()
    engine.set_exit(name, rc)
    engine.poll_once()
    time.sleep(args.hold)  # 끝난 화면을 잠깐 더 보여 주고(웹훅 알림도 이 사이에 나간다) 닫는다
    return rc


def cmd_profiles(args, cfg, profiles):
    for name, p in profiles.items():
        stages = " → ".join(s.name for s in p.stages)
        print(f"{name:10s} {stages}")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # 윈도 콘솔(cp949)에서도 한글 · 기호가 깨지지 않게
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="etacast", description="빌드 · 패키징 진행판")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="로그 파일을 지켜보며 진행판을 연다")
    w.add_argument("--job", action="append", default=[], metavar="[이름=]로그[:프로필]")
    _common(w)
    w.set_defaults(fn=cmd_watch)

    r = sub.add_parser("run", help="명령을 감싸 출력을 보여 주고 진행판을 연다")
    r.add_argument("--name")
    r.add_argument("--profile", default="generic")
    r.add_argument("--encoding", default="utf-8")
    r.add_argument("--hold", type=float, default=10.0, help="끝난 뒤 진행판을 더 띄워 둘 시간(초)")
    _common(r)
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_run)

    pr = sub.add_parser("profiles", help="해석기 목록")
    pr.add_argument("--config")
    pr.set_defaults(fn=cmd_profiles, port=None, host=None, interval=None, title=None)

    args = ap.parse_args(argv)
    cfg = load_config(getattr(args, "config", None))
    profiles = profiles_from(cfg)
    try:
        return args.fn(args, cfg, profiles)
    except KeyboardInterrupt:
        return 130
    except ValueError as e:  # 잘못된 프로필 · 정규식 · 주기
        raise SystemExit(f"설정 오류: {e}")


if __name__ == "__main__":
    sys.exit(main())
