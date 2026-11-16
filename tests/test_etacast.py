"""EtaCast 테스트 — 표준 라이브러리 unittest. `python -m unittest discover -s tests` 로 돈다."""
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etacast import mcp  # noqa: E402
from etacast.config import DEFAULTS, parse_job, profiles_from  # noqa: E402
from etacast.engine import Engine, Job  # noqa: E402
from etacast.profiles import BUILTIN, evaluate, from_dict  # noqa: E402
from etacast.web import make_handler  # noqa: E402

def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


UNREAL_LOG = """BUILD COMMAND STARTED
[1/4] Compile A.cpp
[2/4] Compile B.cpp
"""


class Parsing(unittest.TestCase):
    def test_unreal_build_ratio_and_stage_states(self):
        snap = evaluate(BUILTIN["unreal"], UNREAL_LOG)
        build, cook = snap["stages"][0], snap["stages"][1]
        self.assertEqual(build["state"], "run")
        self.assertAlmostEqual(build["frac"], 0.5)
        self.assertEqual(build["detail"], "2 / 4 파일")
        self.assertEqual(cook["state"], "wait")
        self.assertFalse(snap["finished"])

    def test_unreal_cook_uses_total_group(self):
        text = UNREAL_LOG + "BUILD COMMAND COMPLETED\nCOOK COMMAND STARTED\nCooked packages 30 Packages Remain 70 Total 100\n"
        snap = evaluate(BUILTIN["unreal"], text)
        self.assertEqual(snap["stages"][0]["state"], "done")
        self.assertAlmostEqual(snap["stages"][1]["frac"], 0.3)
        self.assertEqual(snap["stages"][1]["detail"], "30 / 100 에셋")

    def test_unreal_finish_and_fail(self):
        ok = evaluate(BUILTIN["unreal"], UNREAL_LOG + "BUILD SUCCESSFUL\n")
        self.assertTrue(ok["finished"])
        self.assertTrue(all(s["state"] == "done" for s in ok["stages"]))
        bad = evaluate(BUILTIN["unreal"], UNREAL_LOG + "[pkg] done code=1\n")
        self.assertTrue(bad["failed"])

    def test_generic_ninja_and_cargo_and_percent(self):
        self.assertAlmostEqual(evaluate(BUILTIN["generic"], "[3/6] link")["stages"][0]["frac"], 0.5)
        cargo = "    Building [=====>      ] 12/345: serde\n"
        self.assertAlmostEqual(evaluate(BUILTIN["generic"], cargo)["stages"][0]["frac"], 12 / 345)
        self.assertAlmostEqual(evaluate(BUILTIN["generic"], "bundle 40% done")["stages"][0]["frac"], 0.4)

    def test_diagnostics_collected(self):
        snap = evaluate(BUILTIN["generic"], "ok\nerror C2143: boom\nwarning C4996: old\n")
        self.assertEqual(len(snap["diagnostics"]), 2)

    def test_bad_regex_reports_where(self):
        with self.assertRaises(ValueError) as cm:
            from_dict({"name": "mine", "stages": [{"key": "a", "progress": [{"pattern": "("}]}]})
        self.assertIn("mine/a", str(cm.exception))

    def test_custom_profile_from_json(self):
        p = from_dict({"name": "lua", "stages": [{"key": "bake", "name": "굽기", "weight": 2,
                                                  "progress": [{"kind": "ratio", "pattern": r"(\d+) of (\d+)"}]}]})
        snap = evaluate(p, "bake 3 of 4")
        self.assertAlmostEqual(snap["stages"][0]["frac"], 0.75)


class JobSpec(unittest.TestCase):
    def test_windows_drive_letter_is_not_a_profile(self):
        profs = profiles_from(dict(DEFAULTS))
        name, path, prof = parse_job(r"C:\logs\build.log", profs)
        self.assertEqual(name, "build")
        self.assertEqual(prof, "generic")
        self.assertTrue(path.lower().startswith("c:"))

    def test_name_and_profile_suffix(self):
        profs = profiles_from(dict(DEFAULTS))
        name, path, prof = parse_job(r"패키징=C:\logs\pkg.log:unreal", profs)
        self.assertEqual((name, prof), ("패키징", "unreal"))


class Eta(unittest.TestCase):
    def _engine(self, hist=None):
        d = tempfile.mkdtemp()
        e = Engine(history_path=os.path.join(d, "h.json"))
        if hist:
            e.history = hist
        return e

    def test_unknown_at_start_then_measured(self):
        e = self._engine()
        log = os.path.join(tempfile.mkdtemp(), "b.log")
        open(log, "w", encoding="utf-8").write("BUILD COMMAND STARTED\n[1/10] a\n")
        job = Job("b", BUILTIN["unreal"], log)
        e.add_job(job)
        e.poll_once(now=100.0)
        self.assertIsNone(job.snapshot["total_eta"])  # 처음엔 재는 중
        open(log, "a", encoding="utf-8").write("[5/10] e\n")
        e.poll_once(now=110.0)  # 10초에 0.1 → 0.5: 속도 0.08/s, 남은 0.5/0.08 = 6.25초
        build = job.snapshot["stages"][0]
        self.assertAlmostEqual(build["eta"], 12.5, places=2)

    def test_history_gives_estimate_for_waiting_stages(self):
        hist = {"unreal": {"build": [100.0], "cook": [400.0], "stage": [200.0], "archive": [50.0]}}
        e = self._engine(hist)
        log = os.path.join(tempfile.mkdtemp(), "b.log")
        open(log, "w", encoding="utf-8").write("BUILD COMMAND STARTED\n[1/2] a\n")
        job = Job("b", BUILTIN["unreal"], log)
        e.add_job(job)
        e.poll_once(now=0.0)
        # 빌드 절반 남음(50초) + 쿠크 400 + 스테이지 200 + 내보내기 50 = 700
        e.poll_once(now=0.5)
        self.assertAlmostEqual(job.snapshot["total_eta"], 50.0 + 400 + 200 + 50, places=0)

    def test_exit_code_marks_failure_and_learns_on_finish(self):
        e = self._engine()
        log = os.path.join(tempfile.mkdtemp(), "g.log")
        open(log, "w", encoding="utf-8").write("hello\n")
        job = Job("g", BUILTIN["generic"], log)
        e.add_job(job)
        e.poll_once(now=0.0)
        e.set_exit("g", 3)
        e.poll_once(now=5.0)
        self.assertEqual(job.snapshot["status"], "fail")
        self.assertEqual(e.events[-1]["status"], "fail")

    def test_interval_validation(self):
        e = self._engine()
        e.set_interval(0.5)
        self.assertEqual(e.interval, 0.5)
        with self.assertRaises(ValueError):
            e.set_interval(0)


class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(history_path=os.path.join(tempfile.mkdtemp(), "h.json"))
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.engine, "테스트"))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body

    def test_page_and_status(self):
        st, body = self._req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("테스트".encode("utf-8"), body)
        st, body = self._req("GET", "/api/status")
        self.assertEqual(st, 200)
        self.assertIn("interval", json.loads(body))

    def test_pause_and_bad_interval(self):
        self.assertEqual(self._req("POST", "/api/pause")[0], 200)
        self.assertTrue(self.engine.paused)
        self._req("POST", "/api/resume")
        self.assertFalse(self.engine.paused)
        self.assertEqual(self._req("POST", "/api/interval?sec=0")[0], 400)


class Mcp(unittest.TestCase):
    def test_initialize_and_tools_list(self):
        srv = mcp.Server(dict(DEFAULTS), profiles_from(dict(DEFAULTS)))
        out = io.StringIO()
        with redirect_stdout(out):
            mcp.handle(srv, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            mcp.handle(srv, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            mcp.handle(srv, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)  # 알림에는 답하지 않는다
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "etacast")
        names = {t["name"] for t in lines[1]["result"]["tools"]}
        self.assertEqual(names, {"etacast_status", "etacast_tail", "etacast_start", "etacast_wait"})


@unittest.skipUnless(os.environ.get("ETACAST_SAMPLE_LOG"), "ETACAST_SAMPLE_LOG 로그가 있을 때만")
class RealLog(unittest.TestCase):
    def test_sample_log_is_read(self):
        text = open(os.environ["ETACAST_SAMPLE_LOG"], encoding="utf-8", errors="replace").read()
        snap = evaluate(BUILTIN["unreal"], text)
        self.assertEqual(len(snap["stages"]), 4)


if __name__ == "__main__":
    unittest.main()
