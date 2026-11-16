"""진행 엔진 — 로그를 주기적으로 읽고, 남은 시간을 셈하고, 기록을 쌓아 다음번 셈을 고친다.

남은 시간은 세 가지 근거 중 가장 나은 것을 쓴다(위에서부터):
  1. 이 단계를 지금 보고 있는 속도(최근 60초)
  2. 지난 실행들에서 이 단계가 걸린 평균 시간(history.json — 같은 프로필끼리 공유)
  3. 이번 실행에서 지금까지 걸린 시간 ÷ 끝낸 무게(처음 실행 때의 어림)
기록이 없는 처음에는 "재는 중"으로 둔다 — 틀린 숫자를 먼저 보이지 않기 위해서.
"""
import json
import os
import threading
import time
import urllib.request
from collections import deque

from .profiles import evaluate

WINDOW = 60.0
HISTORY_KEEP = 8


def default_history_path():
    return os.path.join(os.path.expanduser("~"), ".etacast", "history.json")


def load_history(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_history(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass  # 기록을 못 쓰면 셈만 덜 똑똑해진다 — 진행판은 계속 돈다


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def send_webhook(url, fmt, text):
    """끝 · 실패 알림. Discord는 content, Slack은 text 필드를 쓴다. 실패해도 조용히 넘긴다."""
    body = json.dumps({"content": text} if fmt == "discord" else {"text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                          "User-Agent": "etacast"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        pass


class Job:
    def __init__(self, name, profile, path=None):
        self.name = name
        self.profile = profile
        self.path = path
        self.exit_code = None
        self.started_at = None
        self.finished_at = None
        self.seen = {}      # 단계 키 → {"start": t, "end": t}
        self.rates = {}     # 단계 키 → deque[(t, frac)]
        self.notified = False
        self.snapshot = None


class Engine:
    def __init__(self, interval=2.0, history_path=None, notify=None, clock=time.time):
        self.jobs = {}
        self.lock = threading.RLock()
        self.interval = float(interval)
        self.paused = False
        self.history_path = history_path or default_history_path()
        self.history = load_history(self.history_path)
        self.notify = notify          # 함수(job_name, status, text) — 웹훅 등
        self.clock = clock
        self.events = []              # 끝 · 실패 사건 목록(브라우저 알림용)
        self._stop = threading.Event()

    # ---- 작업 ------------------------------------------------------------
    def add_job(self, job):
        with self.lock:
            job.started_at = self.clock()
            self.jobs[job.name] = job

    def set_exit(self, name, code):
        """`etacast run` · MCP 시작처럼 프로세스를 직접 돌릴 때 종료 코드를 알려 준다."""
        with self.lock:
            if name in self.jobs:
                self.jobs[name].exit_code = code

    # ---- 조종 ------------------------------------------------------------
    def set_interval(self, sec):
        sec = float(sec)
        if sec <= 0:
            raise ValueError("갱신 주기는 0보다 커야 한다")
        self.interval = sec

    def pause(self, flag=True):
        self.paused = flag

    # ---- 한 번 읽기 --------------------------------------------------------
    def poll_once(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            for job in list(self.jobs.values()):
                self._poll_job(job, now)

    def _poll_job(self, job, now):
        text = read_text(job.path) if job.path else ""
        snap = evaluate(job.profile, text)
        stages = snap["stages"]
        if job.exit_code is not None:
            finished, failed = True, job.exit_code != 0 or snap["failed"]
        else:
            finished, failed = snap["finished"] or snap["failed"], snap["failed"]
        if finished and not failed:  # 종료 코드 0으로 끝났으면 남은 단계도 끝난 것으로 본다
            for s in stages:
                s["state"], s["frac"] = "done", 1.0
        self._timing(job, stages, now)
        est = self._estimate(job, stages, now)
        status = "fail" if failed else ("done" if finished else ("run" if any(s["state"] != "wait" for s in stages) else "wait"))
        total_w = sum(s["weight"] for s in stages) or 1.0
        frac = sum(s["weight"] * s["frac"] for s in stages) / total_w
        job.snapshot = {
            "name": job.name, "profile": job.profile.name, "status": status,
            "frac": frac, "total_eta": None if (finished or failed) else est["total"],
            "elapsed": now - (job.started_at or now),
            "stages": [dict(s, eta=est["per"].get(s["key"])) for s in stages],
            "diagnostics": snap["diagnostics"], "exit_code": job.exit_code,
        }
        if (finished or failed) and not job.notified:
            job.notified = True
            job.finished_at = now
            self._learn(job)
            text_out = f"[EtaCast] {job.name}: {'실패' if failed else '끝'} ({int(now - job.started_at)}초)"
            self.events.append({"id": len(self.events) + 1, "job": job.name, "status": status, "text": text_out})
            if self.notify:
                try:
                    self.notify(job.name, status, text_out)
                except Exception:
                    pass

    def _timing(self, job, stages, now):
        for s in stages:
            if s["state"] == "wait":
                continue
            rec = job.seen.setdefault(s["key"], {"start": now})
            if s["state"] == "done":
                rec.setdefault("end", now)
            if s["state"] == "run":
                dq = job.rates.setdefault(s["key"], deque())
                dq.append((now, s["frac"]))
                while dq and now - dq[0][0] > WINDOW:
                    dq.popleft()

    def _estimate(self, job, stages, now):
        hist = self.history.get(job.profile.name, {})
        done_t = done_w = run_w = 0.0
        run_elapsed = None
        for s in stages:
            rec = job.seen.get(s["key"])
            if s["state"] == "done" and rec and "end" in rec:
                done_t += rec["end"] - rec["start"]
                done_w += s["weight"]
            elif s["state"] == "done":
                done_w += s["weight"]
            elif s["state"] == "run" and rec:
                run_w += s["weight"] * s["frac"]
                run_elapsed = now - rec["start"]
        pace = None
        spent = done_t + (run_elapsed or 0.0)
        if done_w + run_w > 0 and spent > 0:
            pace = spent / (done_w + run_w)  # 무게 1당 초
        per, total = {}, 0.0
        for s in stages:
            if s["state"] == "done":
                per[s["key"]] = 0.0
                continue
            avg = self._avg(hist, s["key"])
            if s["state"] == "run":
                dq = job.rates.get(s["key"], ())
                rate = 0.0
                if len(dq) > 1 and dq[-1][0] > dq[0][0]:
                    rate = (dq[-1][1] - dq[0][1]) / (dq[-1][0] - dq[0][0])
                if rate > 1e-6:
                    left = (1 - s["frac"]) / rate
                elif avg is not None:
                    left = avg * (1 - s["frac"])
                elif pace is not None:
                    left = pace * s["weight"] * (1 - s["frac"])
                else:
                    left = None
            else:
                if avg is not None:
                    left = avg
                elif pace is not None:
                    left = pace * s["weight"]
                else:
                    left = None
            per[s["key"]] = left
            if left is None:
                total = None
            elif total is not None:
                total += left
        return {"per": per, "total": total}

    def _avg(self, hist, key):
        vals = hist.get(key)
        if not vals:
            return None
        return sum(vals) / len(vals)

    def _learn(self, job):
        """끝난 실행의 단계별 걸린 시간을 기록에 넣는다(같은 프로필 · 같은 단계끼리)."""
        prof = self.history.setdefault(job.profile.name, {})
        for key, rec in job.seen.items():
            if "end" in rec and rec["end"] > rec["start"]:
                lst = prof.setdefault(key, [])
                lst.append(round(rec["end"] - rec["start"], 1))
                del lst[:-HISTORY_KEEP]
        save_history(self.history_path, self.history)

    # ---- 바깥으로 ----------------------------------------------------------
    def status(self):
        with self.lock:
            jobs = []
            for j in self.jobs.values():
                if j.snapshot is None:
                    j.snapshot = {"name": j.name, "profile": j.profile.name, "status": "wait", "frac": 0.0,
                                  "total_eta": None, "elapsed": 0.0, "stages": [], "diagnostics": [],
                                  "exit_code": None}
                jobs.append(j.snapshot)
            return {"interval": self.interval, "paused": self.paused, "now": time.strftime("%H:%M:%S"),
                    "jobs": jobs, "events": self.events[-10:]}

    def stop(self):
        self._stop.set()

    def loop(self):
        """백그라운드 루프 — 주기는 매번 다시 읽는다(웹에서 바꾸면 바로 적용)."""
        while not self._stop.is_set():
            if not self.paused:
                self.poll_once()
            self._stop.wait(self.interval)
