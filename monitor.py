# -*- coding: utf-8 -*-
# Network monitor backend: pings targets, detects fluctuation, logs CSV,
# serves JSON API + dashboard on http://127.0.0.1:8787
import os
import re
import sys
import json
import time
import traceback
import threading
import subprocess
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
CONF = os.path.join(BASE, "config.txt")
PORT = 8787
RUNLOG = os.path.join(DATA, "run.log")

# ---------- config ----------
DEFAULT_TARGETS = [
    {"name": "网关", "host": "192.168.1.1", "kind": "local"},
    {"name": "外网-阿里DNS", "host": "223.5.5.5", "kind": "internet"},
    {"name": "外网-腾讯DNS", "host": "119.29.29.29", "kind": "internet"},
]
INTERVAL = 5          # seconds between rounds
TIMEOUT = 3000        # ping wait ms
FLUCT_MULT = 1.8      # latency spike factor vs recent average
FLUCT_ABS = 60        # or absolute ms above recent average
DOWN_CONSEC = 3       # consecutive timeouts -> mark down

def setup_runlog():
    os.makedirs(DATA, exist_ok=True)
    logf = open(RUNLOG, "a", encoding="utf-8", buffering=1)

    class Tee(object):
        def __init__(self, stream):
            self.stream = stream
        def write(self, data):
            try:
                logf.write(data)
                logf.flush()
            except Exception:
                pass
            try:
                if self.stream is not None:
                    self.stream.write(data)
            except Exception:
                pass
        def flush(self):
            try:
                logf.flush()
            except Exception:
                pass
            try:
                if self.stream is not None:
                    self.stream.flush()
            except Exception:
                pass

    sys.stdout = Tee(sys.__stdout__)
    sys.stderr = Tee(sys.__stderr__)

    def _hook(exc_type, exc, tb):
        print("FATAL:\n" + "".join(traceback.format_exception(exc_type, exc, tb)), flush=True)
    sys.excepthook = _hook

def load_config():
    targets = []
    if os.path.exists(CONF):
        with open(CONF, "r", encoding="utf-8-sig") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or ln.startswith("["):
                    continue
                parts = [p.strip() for p in ln.split(",")]
                if len(parts) >= 2:
                    targets.append({"name": parts[0], "host": parts[1], "kind": parts[2] if len(parts) > 2 else "custom"})
    if not targets:
        targets = DEFAULT_TARGETS
    return targets

TARGETS = load_config()
CONFIG_MTIME = os.path.getmtime(CONF) if os.path.exists(CONF) else 0

# ---------- shared state ----------
LOCK = threading.RLock()
FILE_LOCK = threading.Lock()
STATE = {}   # target name -> dict(last_rtt, ok, consec_down, series[])
EVENTS = []  # list of dict(t, target, type, detail)

def empty_state():
    return {"last_rtt": None, "ok": True, "consec_down": 0, "series": []}

for t in TARGETS:
    STATE[t["name"]] = empty_state()

def log_csv(target, rtt, status):
    os.makedirs(DATA, exist_ok=True)
    day = datetime.date.today().isoformat()
    path = os.path.join(DATA, day + ".csv")
    line = "{},{},{},{}\n".format(datetime.datetime.now().strftime("%H:%M:%S"), target, rtt, status)
    with FILE_LOCK:
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig") as f:
            if new:
                f.write("time,target,rtt_ms,status\n")
            f.write(line)

def log_event(target, etype, detail):
    rec = {"t": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "target": target, "type": etype, "detail": detail}
    with LOCK:
        EVENTS.append(rec)
        if len(EVENTS) > 500:
            del EVENTS[:100]
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "events.log")
    with FILE_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write("[{}] {} | {} | {}\n".format(rec["t"], target, etype, detail))
    print("[{}] {} | {} | {}".format(datetime.datetime.now().strftime("%H:%M:%S"), target, etype, detail), flush=True)

def ping(host):
    try:
        r = subprocess.run(
            ["ping", "-n", "1", "-w", str(TIMEOUT), host],
            capture_output=True, timeout=TIMEOUT / 1000 + 1, creationflags=0x08000000
        )
        out = (r.stdout + r.stderr).decode("gbk", errors="ignore")
        m = re.search(r"(?:时间|time)\s*[=<]\s*(\d+)\s*ms", out, re.I)
        if not m:
            m = re.search(r"[=:<]\s*(\d+)\s*ms", out)
        if m:
            return int(m.group(1))
        if re.search(r"TTL|ttl", out):
            return 0
        return None
    except Exception:
        return None

def check_target(name, host):
    rtt = ping(host)
    csv_jobs = []
    ev_jobs = []
    with LOCK:
        st = STATE.get(name)
        if not st:
            return
        if rtt is None:
            st["last_rtt"] = None
            st["consec_down"] += 1
            if st["consec_down"] >= DOWN_CONSEC:
                csv_jobs.append((name, "-1", "down"))
                if st["consec_down"] == DOWN_CONSEC:
                    ev_jobs.append((name, "断线", "连续{}次超时".format(DOWN_CONSEC)))
                st["ok"] = False
            else:
                csv_jobs.append((name, "-1", "timeout"))
                if st["consec_down"] == 1:
                    ev_jobs.append((name, "断线/超时", "连续第1次超时"))
            st["series"].append({"t": time.time(), "r": None})
        else:
            was_down = not st["ok"] or st["consec_down"] >= DOWN_CONSEC
            st["last_rtt"] = rtt
            st["consec_down"] = 0
            st["ok"] = True
            recent = [s["r"] for s in st["series"][-10:] if s["r"] is not None]
            if recent:
                avg = sum(recent) / len(recent)
                if rtt > max(avg * FLUCT_MULT, avg + FLUCT_ABS) and rtt > 60:
                    csv_jobs.append((name, rtt, "fluct"))
                    ev_jobs.append((name, "延迟波动", "{}ms vs 近期均值{}ms".format(rtt, int(avg))))
                else:
                    csv_jobs.append((name, rtt, "ok"))
            else:
                csv_jobs.append((name, rtt, "ok"))
            if was_down:
                ev_jobs.append((name, "恢复", "{}ms".format(rtt)))
            st["series"].append({"t": time.time(), "r": rtt})
        if len(st["series"]) > 1440:
            del st["series"][:100]
    for job in csv_jobs:
        log_csv(*job)
    for job in ev_jobs:
        log_event(*job)

def reload_if_changed():
    global TARGETS, STATE, CONFIG_MTIME
    mt = os.path.getmtime(CONF) if os.path.exists(CONF) else 0
    if mt == CONFIG_MTIME:
        return
    with LOCK:
        newt = load_config()
        newstate = {}
        for t in newt:
            if t["name"] in STATE:
                newstate[t["name"]] = STATE[t["name"]]
            else:
                newstate[t["name"]] = empty_state()
        TARGETS = newt
        STATE = newstate
        CONFIG_MTIME = mt
    log_event("系统", "配置热重载", "已加载 {} 个目标".format(len(newt)))

def worker():
    while True:
        try:
            reload_if_changed()
            with LOCK:
                jobs = [(t["name"], t["host"]) for t in TARGETS]
            threads = []
            for name, host in jobs:
                th = threading.Thread(target=_safe_check, args=(name, host), daemon=True)
                th.start()
                threads.append(th)
            for th in threads:
                th.join()
        except Exception as e:
            print("worker err", e, flush=True)
            traceback.print_exc()
        time.sleep(INTERVAL)

def _safe_check(name, host):
    try:
        check_target(name, host)
    except Exception as e:
        print("err", e, flush=True)
        traceback.print_exc()

# ---------- http ----------
def snapshot():
    with LOCK:
        out = []
        for t in TARGETS:
            st = STATE.get(t["name"]) or empty_state()
            recent = [s["r"] for s in st["series"][-12:] if s["r"] is not None]
            avg_rtt = int(sum(recent) / len(recent)) if recent else None
            out.append({
                "name": t["name"], "host": t["host"], "kind": t["kind"],
                "rtt": st["last_rtt"], "avg": avg_rtt, "ok": st["ok"],
                "down": st["consec_down"],
                "last": st["series"][-1]["t"] * 1000 if st["series"] else None,
            })
        return out

def history(name, minutes):
    csv_pts = history_csv(name, minutes)
    with LOCK:
        st = STATE.get(name)
        mem = []
        if st:
            since = time.time() - minutes * 60
            mem = [{"t": int(s["t"] * 1000), "r": s["r"]} for s in st["series"] if s["t"] >= since]
    if not csv_pts:
        return mem
    last_csv = csv_pts[-1]["t"]
    extra = [p for p in mem if p["t"] > last_csv]
    return csv_pts + extra

def history_csv(name, minutes):
    pts = []
    now = time.time()
    cutoff = now - minutes * 60
    today = datetime.date.today()
    days = [today]
    today0 = datetime.datetime.combine(today, datetime.time()).timestamp()
    if cutoff < today0:
        days.append(today - datetime.timedelta(days=1))
    for d in days:
        p = os.path.join(DATA, d.isoformat() + ".csv")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                for ln in f:
                    parts = ln.rstrip("\r\n").split(",")
                    if len(parts) < 4 or parts[1] != name:
                        continue
                    try:
                        hh, mm, ss = parts[0].split(":")
                        dts = datetime.datetime.combine(d, datetime.time(int(hh), int(mm), int(ss)))
                    except Exception:
                        continue
                    ts = dts.timestamp()
                    if ts < cutoff:
                        continue
                    r = parts[2]
                    pts.append({"t": int(ts * 1000), "r": None if r in ("-1", "") else int(r)})
        except Exception:
            pass
    pts.sort(key=lambda p: p["t"])
    return pts

def history_date(name, date_str):
    """Full-day points for one specific date (YYYY-MM-DD)."""
    pts = []
    try:
        d = datetime.date.fromisoformat(date_str)
    except Exception:
        return pts
    p = os.path.join(DATA, d.isoformat() + ".csv")
    if not os.path.exists(p):
        return pts
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            for ln in f:
                parts = ln.rstrip("\r\n").split(",")
                if len(parts) < 4 or parts[1] != name:
                    continue
                try:
                    hh, mm, ss = parts[0].split(":")
                    dts = datetime.datetime.combine(d, datetime.time(int(hh), int(mm), int(ss)))
                except Exception:
                    continue
                r = parts[2]
                pts.append({"t": int(dts.timestamp() * 1000), "r": None if r in ("-1", "") else int(r)})
    except Exception:
        pass
    pts.sort(key=lambda p: p["t"])
    return pts

def seed_state_from_csv():
    """Reload today's recent samples so 15min/1h charts work after restart."""
    for t in TARGETS:
        pts = history_csv(t["name"], 180)
        if not pts:
            continue
        with LOCK:
            st = STATE[t["name"]]
            st["series"] = [{"t": p["t"] / 1000.0, "r": p["r"]} for p in pts]
            last = pts[-1]
            st["last_rtt"] = last["r"]
            if last["r"] is None:
                st["ok"] = False
                st["consec_down"] = 1
            else:
                st["ok"] = True
                st["consec_down"] = 0

def load_events_file():
    ep = os.path.join(DATA, "events.log")
    if not os.path.exists(ep):
        return
    try:
        with open(ep, "r", encoding="utf-8") as f:
            for ln in f.read().strip().splitlines()[-80:]:
                if len(ln) > 22 and ln.startswith("["):
                    parts = ln[1:].split("] ", 1)
                    if len(parts) == 2:
                        rest = parts[1].split(" | ", 2)
                        EVENTS.append({
                            "t": parts[0],
                            "target": rest[0] if len(rest) > 0 else "",
                            "type": rest[1] if len(rest) > 1 else "",
                            "detail": rest[2] if len(rest) > 2 else "",
                        })
    except Exception:
        pass

def events_date(date_str):
    """All event records for one specific date (YYYY-MM-DD), read from events.log."""
    recs = []
    ep = os.path.join(DATA, "events.log")
    if not os.path.exists(ep):
        return recs
    prefix = "[" + date_str
    try:
        with open(ep, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\r\n")
                if len(ln) > 22 and ln.startswith(prefix):
                    parts = ln[1:].split("] ", 1)
                    if len(parts) == 2:
                        rest = parts[1].split(" | ", 2)
                        recs.append({
                            "t": parts[0],
                            "target": rest[0] if len(rest) > 0 else "",
                            "type": rest[1] if len(rest) > 1 else "",
                            "detail": rest[2] if len(rest) > 2 else "",
                        })
    except Exception:
        pass
    return recs

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/dashboard.html":
            p = os.path.join(BASE, "dashboard.html")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            else:
                self._send(404, "text/plain", b"dashboard.html missing")
        elif path.startswith("/api/now"):
            body = json.dumps({"time": int(time.time() * 1000), "targets": snapshot()}).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/history"):
            q = self.path.split("?", 1)[-1] if "?" in self.path else ""
            name = ""
            minutes = 60
            date_str = ""
            for kv in q.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k == "name":
                        name = unquote(v).replace("+", " ")
                    elif k == "minutes":
                        try:
                            minutes = int(v)
                        except Exception:
                            pass
                    elif k == "date":
                        date_str = unquote(v).replace("+", " ")
            if date_str:
                body = json.dumps(history_date(name, date_str)).encode("utf-8")
            else:
                body = json.dumps(history(name, minutes)).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/events"):
            q = self.path.split("?", 1)[-1] if "?" in self.path else ""
            date_str = ""
            for kv in q.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k == "date":
                        date_str = unquote(v).replace("+", " ")
            if date_str:
                body = json.dumps(events_date(date_str)).encode("utf-8")
            else:
                with LOCK:
                    body = json.dumps(EVENTS[-80:]).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/targets"):
            with LOCK:
                body = json.dumps(TARGETS).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/info"):
            body = json.dumps({"data": DATA, "port": PORT}).encode("utf-8")
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not found")

def start_http():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError as e:
        print("failed to bind 127.0.0.1:{}: {}".format(PORT, e), flush=True)
        sys.exit(1)
    print("dashboard: http://127.0.0.1:{}".format(PORT), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    setup_runlog()
    os.makedirs(DATA, exist_ok=True)
    load_events_file()
    seed_state_from_csv()
    print("[{}] netmon started, python={}, targets: {}".format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        sys.executable,
        ",".join(t["name"] for t in TARGETS),
    ), flush=True)
    threading.Thread(target=worker, daemon=True).start()
    start_http()
