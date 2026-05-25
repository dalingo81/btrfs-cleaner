#!/usr/bin/env python3
# BtrFS Cleaner v1.2.0 - Flask web server (port mode, no auth - fnOS handles it)
import os, re, sys, json, time, threading, uuid, logging, secrets, subprocess
from pathlib import Path
from datetime import datetime

from flask import Flask, render_template_string, jsonify, Response, request, abort

# === Config ===
PORT = int(os.environ.get('BTRFS_CLEANER_PORT', 5100))
HOST = os.environ.get('BTRFS_CLEANER_HOST', '0.0.0.0')

DATA_DIR = Path(os.environ.get('TRIM_PKGVAR', '/tmp/btrfs-cleaner'))
CONF_DIR = Path(os.environ.get('TRIM_PKGETC', DATA_DIR / 'config'))
LOG_DIR = Path(os.environ.get('BTRFS_CLEANER_LOG_DIR', DATA_DIR / 'log'))
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, style='{', format='{asctime} [{levelname}] {message}',
                    handlers=[logging.FileHandler(LOG_DIR / 'app.log'), logging.StreamHandler(sys.stdout)])
log = logging.getLogger('btrfs-cleaner')

SCRIPT_DIR = Path(__file__).resolve().parent
WWW_DIR = SCRIPT_DIR.parent / 'www'

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# === Security: only allow requests from localhost (via proxy.cgi) ===
@app.before_request
def check_local_access():
    """拒绝所有非本地请求。外部通过 proxy.cgi 访问，请求来自 localhost。"""
    remote = request.remote_addr
    # IPv4, IPv6 localhost, and IPv4-mapped-on-IPv6
    if remote not in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        log.warning(f'Blocked external request from {remote}')
        abort(403, 'Access denied - use proxy.cgi')

# === Btrfs Helpers ===
def run_btrfs(*args, timeout=30):
    try:
        r = subprocess.run(['sudo', 'btrfs'] + list(args), capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired: return '', 'timeout', -1
    except FileNotFoundError: return '', 'btrfs not found', -2

def find_mount_for_device(dev_path):
    try:
        for line in Path('/proc/mounts').read_text().splitlines():
            p = line.split()
            if len(p) >= 2 and p[0] == dev_path: return p[1]
    except: pass
    return None

def fmt_bytes(b):
    for u in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        if abs(b) < 1024: return f'{b:.1f} {u}'
        b /= 1024
    return f'{b:.1f} PiB'

def get_usage(mp):
    try:
        s = os.statvfs(mp)
        total = s.f_blocks * s.f_frsize
        avail = s.f_bavail * s.f_frsize
        used = total - avail
        pn = round(used / total * 100, 1) if total > 0 else 0
        return {'total': fmt_bytes(total), 'used': fmt_bytes(used), 'avail': fmt_bytes(avail),
                'percent': f'{pn}%', 'percent_num': pn}
    except: return {}

def get_btrfs_filesystems():
    stdout, stderr, rc = run_btrfs('filesystem', 'show', '-d')
    if rc != 0: return []
    fs_list, cur = [], None
    for line in stdout.splitlines():
        line = line.strip()
        if not line: continue
        if line.startswith('Label:'):
            if cur and cur.get('uuid'): fs_list.append(cur)
            cur = {'label': '', 'uuid': '', 'devices': [], 'total': '', 'used': '', 'avail': '', 'use_percent': '?'}
            lm = re.search(r"Label:\s*(?:'([^']*)'|(\S+))", line)
            um = re.search(r'uuid:\s*(\S+)', line)
            if lm: cur['label'] = lm.group(1) or lm.group(2) or ''
            if um: cur['uuid'] = um.group(1)
        elif cur and re.match(r'^\s*devid', line):
            dm = re.search(r'path\s+(\S+)', line)
            sm = re.search(r'size\s+([\d.]+\s*\w+)', line)
            if dm: cur.setdefault('devices', []).append({'path': dm.group(1), 'size': sm.group(1) if sm else '?'})
    if cur and cur.get('uuid'): fs_list.append(cur)
    for fs in fs_list:
        for dev in fs.get('devices', []):
            mp = find_mount_for_device(dev['path'])
            if mp:
                u = get_usage(mp); fs['mountpoint'] = mp; fs.update(u); break
    return fs_list

# === Scrub Management ===
_scrub_jobs = {}
_scrub_lock = threading.Lock()

def _scrub_worker(mountpoint, job_id):
    job = _scrub_jobs.get(job_id)
    if not job: return
    with _scrub_lock: job.update(status='running', started=datetime.now().isoformat(), progress=0, detail='Starting...', _cancel=False)
    try:
        proc = subprocess.Popen(['sudo', 'btrfs', 'scrub', 'start', '-B', mountpoint],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        with _scrub_lock: job['_proc'] = proc
        while proc.poll() is None:
            with _scrub_lock:
                if job.get('_cancel'):
                    # Cancel at kernel level first, then terminate -B
                    subprocess.run(['sudo', 'btrfs', 'scrub', 'cancel', mountpoint],
                        capture_output=True, timeout=10)
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except: proc.kill(); proc.wait()
                    job.update(status='cancelled', detail='Cancelled', finished=datetime.now().isoformat())
                    return
            try:
                r = subprocess.run(['sudo', 'btrfs', 'scrub', 'status', '-d', mountpoint],
                    capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    for l in r.stdout.splitlines():
                        m = re.search(r'(\d+\.?\d*)%', l)
                        if m:
                            with _scrub_lock: job['progress'] = float(m.group(1))
            except: pass
            time.sleep(1)
        proc.wait()
        with _scrub_lock:
            job['_proc'] = None; job['progress'] = 100
            # Check if we were cancelled (btrfs scrub cancel makes -B return non-zero)
            if job.get('_cancel'):
                job.update(status='cancelled', detail='Cancelled', finished=datetime.now().isoformat())
            elif proc.returncode == 0:
                job.update(status='done', detail='Completed', finished=datetime.now().isoformat())
            else:
                job.update(status='error', detail=f'Exit={proc.returncode}', finished=datetime.now().isoformat())
    except Exception as e:
        with _scrub_lock: job.update(status='error', detail=str(e), finished=datetime.now().isoformat())

def _trigger_scrub(mp):
    jid = 'auto_' + uuid.uuid4().hex[:8]
    with _scrub_lock: _scrub_jobs[jid] = {'id': jid, 'mountpoint': mp, 'status': 'pending', 'progress': 0, 'detail': '', 'started': None, 'finished': None}
    threading.Thread(target=_scrub_worker, args=(mp, jid), daemon=True).start()

# === Schedule ===
_scheduler = None
_scrub_schedules = {}
_SCHED_FILE = DATA_DIR / 'schedules.json'

def _load_schedules():
    try:
        if _SCHED_FILE.exists(): _scrub_schedules.update(json.loads(_SCHED_FILE.read_text()))
    except: pass

def _save_schedules():
    try:
        _SCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SCHED_FILE.write_text(json.dumps(_scrub_schedules))
    except: pass

def _init_scheduler():
    global _scheduler
    if _scheduler: return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler(daemon=True)
        _load_schedules()
        for sid, s in list(_scrub_schedules.items()):
            if s.get('enabled', True):
                try:
                    freq, h, m, mp = s.get('frequency', 'daily'), s.get('hour', 3), s.get('minute', 0), s.get('mountpoint', '')
                    if freq == 'daily': _scheduler.add_job(func=lambda x=mp: _trigger_scrub(x), id=sid, trigger='cron', hour=h, minute=m)
                    elif freq == 'weekly': _scheduler.add_job(func=lambda x=mp: _trigger_scrub(x), id=sid, trigger='cron', day_of_week=s.get('day_of_week', 0), hour=h, minute=m)
                    elif freq == 'monthly': _scheduler.add_job(func=lambda x=mp: _trigger_scrub(x), id=sid, trigger='cron', day=s.get('day_of_month', 1), hour=h, minute=m)
                except: pass
        _scheduler.start()
        import atexit; atexit.register(lambda: _scheduler.shutdown(wait=False) if _scheduler else None)
        log.info('apscheduler initialized successfully')
    except ImportError:
        _scheduler = None
        log.error('apscheduler module not installed. Run: pip3 install apscheduler')
    except Exception as e:
        _scheduler = None
        log.error(f'apscheduler init failed: {e}')

# === API Routes (no auth - fnOS gateway handles it) ===
@app.route('/api/status')
def api_status():
    return jsonify(get_btrfs_filesystems())

@app.route('/api/scrub', methods=['POST'])
def api_start_scrub():
    data = request.get_json()
    mp = data.get('mountpoint', '').strip()
    if not mp: return jsonify({'error': 'Need mountpoint'}), 400
    for j in _scrub_jobs.values():
        if j['mountpoint'] == mp and j['status'] in ('pending', 'running'):
            return jsonify({'error': 'Already running'}), 409
    jid = uuid.uuid4().hex[:12]
    with _scrub_lock: _scrub_jobs[jid] = {'id': jid, 'mountpoint': mp, 'status': 'pending', 'progress': 0, 'detail': '', 'started': None, 'finished': None}
    threading.Thread(target=_scrub_worker, args=(mp, jid), daemon=True).start()
    log.info(f'Manual scrub: {mp}')
    return jsonify({'job_id': jid, 'status': 'started'})

@app.route('/api/scrub/status')
def api_scrub_status():
    with _scrub_lock:
        jobs = sorted([{k: v for k, v in j.items() if k not in ('_proc', '_cancel')} for j in _scrub_jobs.values()],
                      key=lambda x: x.get('started') or '0', reverse=True)
    return jsonify(jobs)

@app.route('/api/scrub/<jid>/cancel', methods=['POST'])
def api_cancel_scrub(jid):
    with _scrub_lock:
        j = _scrub_jobs.get(jid)
        if not j: return jsonify({'error': 'Not found'}), 404
        if j['status'] not in ('pending', 'running'): return jsonify({'error': 'Not running'}), 400
        j['_cancel'] = True
        if j['status'] == 'running':
            j['status'] = 'cancelling'
            # Stop the scrub at kernel level so -B process exits cleanly
            mp = j.get('mountpoint', '')
            if mp:
                subprocess.run(['sudo', 'btrfs', 'scrub', 'cancel', mp], capture_output=True, timeout=10)
        else:
            j['status'] = 'cancelled'
    return jsonify({'ok': True})

@app.route('/api/scrub/<jid>', methods=['DELETE'])
def api_delete_job(jid):
    with _scrub_lock:
        j = _scrub_jobs.get(jid)
        if not j: return jsonify({'error': 'Not found'}), 404
        if j['status'] in ('pending', 'running'): return jsonify({'error': 'Running'}), 400
        del _scrub_jobs[jid]
    return jsonify({'ok': True})

@app.route('/api/schedules', methods=['GET'])
def api_schedules():
    return jsonify(list(_scrub_schedules.values()))

@app.route('/api/schedules', methods=['POST'])
def api_add_schedule():
    data = request.get_json()
    mp = data.get('mountpoint', '').strip()
    freq, hour, minute = data.get('frequency', 'daily'), int(data.get('hour', 3)), int(data.get('minute', 0))
    if not mp: return jsonify({'error': 'Need mountpoint'}), 400
    _init_scheduler()
    if _scheduler is None: return jsonify({'error': 'Scheduler unavailable'}), 500
    sid = 'sched_' + uuid.uuid4().hex[:8]
    if freq == 'daily':
        _scheduler.add_job(func=lambda: _trigger_scrub(mp), id=sid, trigger='cron', hour=hour, minute=minute)
        desc = f'每日 {hour:02d}:{minute:02d}'
    elif freq == 'weekly':
        dow = int(data.get('day_of_week', 0))
        _scheduler.add_job(func=lambda: _trigger_scrub(mp), id=sid, trigger='cron', day_of_week=dow, hour=hour, minute=minute)
        desc = f'每{["周一","周二","周三","周四","周五","周六","周日"][dow]} {hour:02d}:{minute:02d}'
    elif freq == 'monthly':
        dom = int(data.get('day_of_month', 1))
        _scheduler.add_job(func=lambda: _trigger_scrub(mp), id=sid, trigger='cron', day=dom, hour=hour, minute=minute)
        desc = f'每月{dom}日 {hour:02d}:{minute:02d}'
    else: return jsonify({'error': 'Invalid frequency'}), 400
    _scrub_schedules[sid] = {'id': sid, 'mountpoint': mp, 'frequency': freq, 'hour': hour, 'minute': minute,
                              'description': desc, 'enabled': True}
    _save_schedules()
    return jsonify(_scrub_schedules[sid])

@app.route('/api/schedules/<sid>', methods=['DELETE'])
def api_delete_schedule(sid):
    if _scheduler:
        try: _scheduler.remove_job(sid)
        except: pass
    _scrub_schedules.pop(sid, None); _save_schedules()
    return jsonify({'ok': True})

@app.route('/api/me')
def api_me():
    return jsonify({'ok': True, 'user': 'fnos'})

# === Page ===
MAIN_HTML = ''

@app.route('/')
def index():
    global MAIN_HTML
    if not MAIN_HTML:
        p = WWW_DIR / 'index.html'
        MAIN_HTML = p.read_text(encoding='utf-8') if p.exists() else '<html><body>Not found</body></html>'
    return render_template_string(MAIN_HTML, user='BtrFS')

if __name__ == '__main__':
    _init_scheduler()
    log.info(f'BtrFS Cleaner starting on {HOST}:{PORT}')
    try:
        import socket
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(('::', PORT)); s.close()
        bind = '::'
    except: bind = '0.0.0.0'
    app.run(host=bind, port=PORT, debug=False, threaded=True)
