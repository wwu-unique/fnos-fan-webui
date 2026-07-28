#!/usr/bin/env python3
"""fnOS Fan Control Web UI v4.2 - Fixed empty curve bug + mode switching + redesigned layout"""
import os, json, time, threading, logging, subprocess
from pathlib import Path
from collections import deque
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────── CONFIGURATION ───────────────────
def discover_qnap8528_hwmon():
    """Find the qnap8528 hwmon dynamically; indices can drift after boot/update."""
    base = Path('/sys/class/hwmon')
    for candidate in sorted(base.glob('hwmon*')):
        try:
            if (candidate / 'name').read_text().strip() == 'qnap8528' and (candidate / 'pwm1').exists():
                return str(candidate)
        except OSError:
            continue
    return None

HWMON = discover_qnap8528_hwmon()
CPU_HWMON = '/sys/class/hwmon/hwmon3'      # coretemp: temp1-5
NVME0_PATH = '/sys/class/hwmon/hwmon0'     # NVMe0
NVME1_PATH = '/sys/class/hwmon/hwmon1'     # NVMe1
TEMP_ZONE = '/sys/class/thermal/thermal_zone0/temp'
CONFIG_PATH = '/data/curve-config.json'
DISK_TEMPS_PATH = '/data/disk-temps.json'
LOG_PATH = '/data/fan-control.log'
TEMP_LOG_PATH = '/data/temp-history.jsonl'

# ─────────────────── CONTROL PARAMETERS ───────────────────
HYSTERESIS = 2
PWM_DEAD_BAND = 8
PWM_SMOOTHING = 0.45
CONTROL_INTERVAL = 3
MIN_PWM = 50
MAX_PWM = 255
CRITICAL_TEMP = 85
AVERAGE_WINDOW = 5
DISK_TEMP_LOG_SIZE = 300

# ─────────────────── DEFAULT CURVE ───────────────────
DEFAULT_CURVE = [
    {"temp": 35, "pwm": 50}, {"temp": 40, "pwm": 65}, {"temp": 45, "pwm": 80},
    {"temp": 50, "pwm": 100}, {"temp": 55, "pwm": 120}, {"temp": 60, "pwm": 150},
    {"temp": 65, "pwm": 180}, {"temp": 70, "pwm": 210}, {"temp": 75, "pwm": 235},
    {"temp": 80, "pwm": 255},
]

DISK_TEMP_THRESHOLDS = [
    {"temp": 55, "pwm": 180}, {"temp": 60, "pwm": 230}, {"temp": 65, "pwm": 255},
]

# ─────────────────── GLOBAL STATE ───────────────────
CURVE = DEFAULT_CURVE.copy()
manual_mode = False
manual_pwm = 100
pending_manual_pwm = 100
pending_manual_mode = False
temp_history = deque(maxlen=AVERAGE_WINDOW)
current_avg_temp = 0
last_pwm_written = 0
last_trigger_temp = 0

# ─────────────────── LOGGING ───────────────────
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
logger = logging.getLogger('fan-control')

# ─────────────────── HELPERS ───────────────────
def read_sysfs(path):
    try:
        with open(path) as f: return f.read().strip()
    except: return None

def write_sysfs(path, val):
    try:
        with open(path, 'w') as f: f.write(str(val))
        return True
    except: return False

def read_nvme_temp(hwmon_path):
    for attr in ['temp1_input', 'temp2_input']:
        v = read_sysfs(f'{hwmon_path}/{attr}')
        if v:
            try:
                t = int(v)
                if t > 0: return t // 1000
            except: pass
    return None

def read_cpu_temp():
    raw = read_sysfs(TEMP_ZONE)
    if raw:
        try: return int(raw) // 1000
        except: pass
    v = read_sysfs(f'{CPU_HWMON}/temp1_input')
    if v:
        try: return int(v) // 1000
        except: pass
    return 0

def read_board_temps():
    temps = {}
    if not HWMON: return temps
    for t in ['temp1_input', 'temp6_input']:
        v = read_sysfs(f'{HWMON}/{t}')
        if v:
            try:
                val = int(v)
                temp_c = val // 1000
                if 0 < temp_c < 90:
                    temps[t.replace('_input', '')] = temp_c
            except: pass
    return temps

def read_disk_temps():
    try:
        if os.path.exists(DISK_TEMPS_PATH):
            mtime = os.path.getmtime(DISK_TEMPS_PATH)
            if (time.time() - mtime) < 120:
                with open(DISK_TEMPS_PATH) as f:
                    data = json.load(f)
                result = {}
                if 'disks' in data:
                    for disk in data['disks']:
                        if disk.get('present') and disk.get('temp') is not None:
                            result[disk['slot']] = disk['temp']
                result['max_temp'] = data.get('max_temp', 0)
                return result
    except Exception as e:
        logger.warning(f"Disk temps read failed: {e}")
    return {}

def read_fan_info():
    if not HWMON: return {'raw': 0, 'rpm': 0, 'pwm': 0}
    fan_raw = read_sysfs(f'{HWMON}/fan1_input')
    pwm = read_sysfs(f'{HWMON}/pwm1')
    fan_raw_int = int(fan_raw) if fan_raw else 0
    fan_rpm = fan_raw_int
    return {'raw': fan_raw_int, 'rpm': fan_rpm, 'pwm': int(pwm) if pwm else 0}

def compute_averaged_temp(cpu_temp, nvme0_temp, nvme1_temp, disk_max=0):
    max_nvme = max(nvme0_temp or 0, nvme1_temp or 0)
    if max_nvme > 60:
        control_temp = max(cpu_temp * 0.5, max_nvme * 0.8)
    elif max_nvme > cpu_temp:
        control_temp = cpu_temp * 0.7 + max_nvme * 0.3
    elif disk_max > 55:
        control_temp = max(cpu_temp, disk_max * 0.6)
    else:
        control_temp = cpu_temp
    temp_history.append(control_temp)
    if temp_history:
        return round(sum(temp_history) / len(temp_history), 1)
    return control_temp

def get_pwm_from_curve(temp):
    if not CURVE: return MIN_PWM
    if temp <= CURVE[0]["temp"]: return CURVE[0]["pwm"]
    if temp >= CURVE[-1]["temp"]: return CURVE[-1]["pwm"]
    for i in range(len(CURVE) - 1):
        t1, p1 = CURVE[i]["temp"], CURVE[i]["pwm"]
        t2, p2 = CURVE[i+1]["temp"], CURVE[i+1]["pwm"]
        if t1 <= temp <= t2:
            if t2 == t1: return p1
            return int(round(p1 + (p2 - p1) * (temp - t1) / (t2 - t1)))
    return CURVE[-1]["pwm"]

def check_disk_overrides(disk_temps, current_pwm):
    max_disk = max((v for v in disk_temps.values() if isinstance(v, (int, float))), default=0)
    if max_disk == 0: return current_pwm
    override = current_pwm
    for th in DISK_TEMP_THRESHOLDS:
        if max_disk >= th["temp"]: override = max(override, th["pwm"])
    return override

def smooth_pwm(target, current):
    if current == 0: return target
    diff = target - current
    if abs(diff) <= 5: return target
    return int(round(current + diff * PWM_SMOOTHING))

def log_temperature(cpu, nvme0, nvme1, avg, pwm, rpm, disks):
    entry = {"ts": int(time.time()), "time": datetime.now().isoformat(),
             "cpu": cpu, "nvme0": nvme0, "nvme1": nvme1, "avg": avg,
             "pwm": pwm, "rpm": rpm, "disk": disks}
    try:
        with open(TEMP_LOG_PATH, 'a') as f: f.write(json.dumps(entry) + '\n')
        if os.path.exists(TEMP_LOG_PATH):
            with open(TEMP_LOG_PATH) as f: lines = f.readlines()
            if len(lines) > DISK_TEMP_LOG_SIZE:
                with open(TEMP_LOG_PATH, 'w') as f: f.writelines(lines[-DISK_TEMP_LOG_SIZE:])
    except: pass

def get_system_info():
    kernel = read_sysfs('/proc/version') or 'unknown'
    module_loaded = False
    try:
        r = subprocess.run(['lsmod'], capture_output=True, text=True, timeout=5)
        module_loaded = 'qnap8528' in r.stdout
    except: pass
    pwm_exists = os.path.exists(f'{HWMON}/pwm1')
    return {
        'kernel': kernel.split()[2] if len(kernel.split()) > 2 else kernel,
        'hwmon_path': HWMON,
        'module_loaded': module_loaded,
        'pwm_accessible': pwm_exists,
    }

# ─────────────────── CONFIG ───────────────────
def load_config():
    global CURVE
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                data = json.load(f)
                if 'curve' in data and len(data['curve']) >= 2:
                    CURVE = data['curve']
                    return
    except: pass
    CURVE = [p.copy() for p in DEFAULT_CURVE]
    save_config()

def save_config():
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f: json.dump({'curve': CURVE}, f, indent=2)
    except: pass

def validate_curve(new_curve):
    """Validate curve has at least 2 points with valid temp/pwm values."""
    if not new_curve or not isinstance(new_curve, list) or len(new_curve) < 2:
        return False
    for pt in new_curve:
        if not isinstance(pt, dict) or 'temp' not in pt or 'pwm' not in pt:
            return False
        if not isinstance(pt['temp'], (int, float)) or not isinstance(pt['pwm'], (int, float)):
            return False
    return True

def safe_set_curve(new_curve):
    """Only update CURVE if new_curve is valid; otherwise keep current."""
    global CURVE
    if validate_curve(new_curve):
        CURVE = new_curve
    elif not validate_curve(CURVE):
        # Current CURVE is also invalid, restore default
        CURVE = [p.copy() for p in DEFAULT_CURVE]
    # If new_curve is invalid but CURVE is valid, keep CURVE as-is

load_config()

# ─────────────────── STATUS ───────────────────
def get_status():
    cpu = read_cpu_temp()
    nvme0 = read_nvme_temp(NVME0_PATH)
    nvme1 = read_nvme_temp(NVME1_PATH)
    board = read_board_temps()
    disks = read_disk_temps()
    fan = read_fan_info()
    disk_max = disks.get('max_temp', 0)
    avg = compute_averaged_temp(cpu, nvme0, nvme1, disk_max)
    global current_avg_temp
    current_avg_temp = avg
    return {
        'cpu_temp': cpu, 'nvme0_temp': nvme0, 'nvme1_temp': nvme1,
        'board_temps': board, 'avg_temp': avg, 'disk_temps': disks,
        'pwm': fan['pwm'], 'fan_raw': fan['raw'], 'fan_rpm': fan['rpm'],
        'manual_mode': manual_mode, 'manual_pwm': manual_pwm,
        'pending_manual': pending_manual_pwm if pending_manual_mode else None,
        'pending_mode': pending_manual_mode,
        'curve': CURVE, 'hysteresis': HYSTERESIS, 'avg_window': AVERAGE_WINDOW,
        'control_interval': CONTROL_INTERVAL, 'last_pwm': last_pwm_written,
        'pwm_dead_band': PWM_DEAD_BAND, 'pwm_smoothing': PWM_SMOOTHING,
        'min_pwm': MIN_PWM, 'max_pwm': MAX_PWM, 'critical_temp': CRITICAL_TEMP,
        'disk_thresholds': DISK_TEMP_THRESHOLDS,
        'system': get_system_info(),
    }

# ─────────────────── CONTROL LOOP ───────────────────
def control_loop():
    global manual_mode, manual_pwm, last_pwm_written, last_trigger_temp
    logger.info(f"Fan control v4.2 started (interval={CONTROL_INTERVAL}s, dead_band={PWM_DEAD_BAND}, smoothing={PWM_SMOOTHING})")
    while True:
        try:
            cpu = read_cpu_temp()
            nvme0 = read_nvme_temp(NVME0_PATH)
            nvme1 = read_nvme_temp(NVME1_PATH)
            disks = read_disk_temps()
            disk_max = disks.get('max_temp', 0)
            avg = compute_averaged_temp(cpu, nvme0, nvme1, disk_max)
            fan = read_fan_info()

            if manual_mode:
                target = manual_pwm
                write_sysfs(f'{HWMON}/pwm1', target)
                last_pwm_written = target
                pwm = target
            else:
                target = get_pwm_from_curve(avg)
                target = max(target, check_disk_overrides(disks, target))
                max_temp = max(cpu, nvme0 or 0, nvme1 or 0, disk_max)
                if max_temp >= CRITICAL_TEMP:
                    target = MAX_PWM
                    logger.warning(f"CRITICAL: max temp {max_temp}°C >= {CRITICAL_TEMP}°C, forcing max PWM")
                target = max(MIN_PWM, min(MAX_PWM, target))
                current = fan['pwm']
                temp_diff = avg - last_trigger_temp
                if last_trigger_temp == 0:
                    last_trigger_temp = avg
                elif abs(temp_diff) >= HYSTERESIS:
                    last_trigger_temp = avg
                if abs(target - current) <= PWM_DEAD_BAND:
                    target = current
                else:
                    target = smooth_pwm(target, current)
                target = max(MIN_PWM, min(MAX_PWM, target))
                pwm = target
                write_sysfs(f'{HWMON}/pwm1', pwm)
                last_pwm_written = pwm

            log_temperature(cpu, nvme0, nvme1, avg, pwm, fan['rpm'], disks)
            if int(time.time()) % 30 < CONTROL_INTERVAL:
                logger.info(f"CPU:{cpu}°C NVMe:{nvme0}/{nvme1}°C Disk:{disk_max}°C Avg:{avg}°C PWM:{pwm}/255 RPM:{fan['rpm']}")
        except Exception as e:
            logger.error(f"Control loop error: {e}")
        time.sleep(CONTROL_INTERVAL)

# ─────────────────── HTTP HANDLER ───────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path == '/api/status':
            self.send_json(get_status())
        elif self.path == '/api/log':
            try:
                if os.path.exists(TEMP_LOG_PATH):
                    with open(TEMP_LOG_PATH) as f: lines = f.readlines()[-80:]
                    self.send_json({'log': [json.loads(l) for l in lines]})
                else: self.send_json({'log': []})
            except: self.send_json({'log': []})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        global CURVE, manual_mode, manual_pwm, pending_manual_pwm, pending_manual_mode
        length = int(self.headers.get('Content-Length', 0))
        data = json.loads(self.rfile.read(length)) if length > 0 else {}
        if self.path == '/api/curve':
            safe_set_curve(data.get('curve', CURVE))
            manual_mode = data.get('manual_mode', False)
            manual_pwm = data.get('manual_pwm', 100)
            save_config()
            if manual_mode: write_sysfs(f'{HWMON}/pwm1', manual_pwm)
            self.send_json({'ok': True})
        elif self.path == '/api/set-pwm':
            manual_mode = data.get('manual_mode', False)
            if manual_mode:
                manual_pwm = data['pwm']
                write_sysfs(f'{HWMON}/pwm1', manual_pwm)
            else: safe_set_curve(data.get('curve', CURVE))
            save_config(); self.send_json({'ok': True})
        elif self.path == '/api/set-pwm-pending':
            pending_manual_pwm = data.get('pwm', 100)
            pending_manual_mode = data.get('manual_mode', False)
            safe_set_curve(data.get('curve', CURVE))
            save_config(); self.send_json({'ok': True})
        elif self.path == '/api/apply':
            if pending_manual_mode:
                manual_mode = True; manual_pwm = pending_manual_pwm
                write_sysfs(f'{HWMON}/pwm1', manual_pwm)
            else:
                manual_mode = False
            self.send_json({'ok': True, 'manual_mode': manual_mode,
                'manual_pwm': manual_pwm if manual_mode else None,
                'actual_pwm': int(read_sysfs(f'{HWMON}/pwm1') or 0)})
        else: self.send_response(404); self.end_headers()

    def send_json(self, obj):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def log_message(self, *a): pass

# ─────────────────── HTML UI (v4.1 - Redesigned Layout) ───────────────────
HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FNOS 风扇控制台</title><style>:root{--bg:#f3f6fa;--p:#fff;--ink:#10243e;--mut:#688;--line:#dce5ee;--blue:#1769df;--green:#07805b;--amber:#b86a00;--shadow:0 8px 25px #173b6810}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,"Microsoft YaHei",sans-serif}.app{max-width:1360px;margin:auto;padding:20px}.head{height:54px;display:flex;align-items:center;justify-content:space-between}.brand small{font-weight:800;letter-spacing:.12em;color:var(--blue);font-size:10px}.brand h1{font-size:21px;margin:1px 0}.online{background:#e7f8f0;color:var(--green);font-weight:750;padding:8px 12px;border-radius:99px}.online:before{content:'●';margin-right:6px}.metrics{display:grid;grid-template-columns:1.35fr repeat(4,1fr);gap:10px;margin:8px 0 14px}.metric,.panel{background:var(--p);border:1px solid var(--line);border-radius:13px;box-shadow:var(--shadow)}.metric{padding:13px 15px;min-height:84px}.metric .label,.muted{font-size:12px;color:#687b90}.metric b{display:block;font-size:26px;letter-spacing:-.05em;margin-top:3px}.metric.hero{background:linear-gradient(120deg,#fff,#eaf3ff)}.metric.hero b{font-size:38px;color:var(--blue)}.main{display:grid;grid-template-columns:minmax(620px,8fr) minmax(300px,4fr);gap:14px}.panel{padding:17px}.ph{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:13px}.ph h2{font-size:15px;margin:0}.ph p{margin:2px 0 0;font-size:12px;color:#687b90}.seg{background:#edf2f7;padding:3px;border-radius:9px}.seg button,.btn{border:0;font:inherit;cursor:pointer;border-radius:7px;font-weight:750}.seg button{padding:7px 10px;background:transparent;color:#607287}.seg button.on{background:#fff;color:var(--blue);box-shadow:0 1px 4px #1232}.curve{height:270px;border:1px solid var(--line);border-radius:10px;background:#fbfdff;padding:12px}.curve svg{width:100%;height:100%;overflow:visible}.g{stroke:#e4ebf3}.axis{fill:#718398;font-size:10px}.line{fill:none;stroke:var(--blue);stroke-width:3}.pt{fill:white;stroke:var(--blue);stroke-width:3}.now{fill:#0da878;stroke:#fff;stroke-width:3}.editor{margin-top:12px;border:1px solid var(--line);border-radius:10px;overflow:hidden}.erow{height:42px;display:grid;grid-template-columns:36px 92px 62px 1fr;gap:10px;align-items:center;padding:0 12px;border-bottom:1px solid #edf1f5}.erow:last-child{border:0}.erow .n{color:#8493a3}.erow b{color:var(--blue);font-size:13px}.erow input{width:100%;accent-color:var(--blue)}.manual{display:none;padding:20px 10px}.manual.show{display:block}.manual b{font-size:30px;color:var(--blue)}.save{position:sticky;bottom:0;margin-top:12px;background:#f0f5fb;border:1px solid #d7e3f0;padding:10px;border-radius:9px;display:flex;align-items:center;justify-content:space-between;z-index:2}.btn{padding:9px 12px}.secondary{background:#fff;border:1px solid var(--line);color:#486078}.primary{background:var(--blue);color:white}.primary:disabled{opacity:.4}.right{display:flex;flex-direction:column;gap:14px}.sensors{display:grid;grid-template-columns:1fr 1fr;gap:9px}.sensor{padding:12px;border:1px solid var(--line);border-radius:9px}.sensor span{display:block;font-size:12px;color:#687b90}.sensor b{font-size:22px}.live{margin-top:12px;padding:12px;background:#edf7f3;border-radius:9px;color:#235d4d}.live strong{display:block;margin-top:2px}.mini{height:170px;border:1px solid var(--line);border-radius:9px;margin-top:12px}.diag{border:1px solid var(--line);border-radius:9px;padding:11px;color:#52687e;font-size:12px}.diag summary{cursor:pointer;font-weight:700}.diag div{padding-top:8px;display:grid;gap:5px}.diag code{word-break:break-all}.history{margin-top:14px}.history canvas{width:100%;height:250px;border:1px solid var(--line);border-radius:10px;background:#fbfdff}.toast{position:fixed;right:20px;bottom:20px;background:#10243e;color:#fff;padding:11px 14px;border-radius:8px;opacity:0;transition:.2s}.toast.on{opacity:1}@media(max-width:900px){.main{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.metric.hero{grid-column:span 2}.app{padding:14px}}@media(max-width:520px){.metrics{gap:7px}.metric{padding:10px}.metric b{font-size:21px}.main{gap:10px}.erow{grid-template-columns:26px 78px 52px 1fr;padding:0 8px;gap:5px}.head{height:auto;margin-bottom:8px}}</style></head><body><main class="app"><header class="head"><div class="brand"><small>FNOS · THERMAL CONTROL</small><h1>风扇控制台</h1></div><div class="online" id="online">控制器在线</div></header><section class="metrics"><div class="metric hero"><span class="label">当前控制温度</span><b id="avg">--°C</b><span class="muted" id="source">等待传感器</span></div><div class="metric"><span class="label">风扇转速</span><b id="rpm">--</b><span class="muted">RPM · 实时反馈</span></div><div class="metric"><span class="label">风扇输出</span><b id="out">--%</b><span class="muted" id="raw">PWM -- / 255</span></div><div class="metric"><span class="label">最高传感器</span><b id="hot">--°C</b><span class="muted" id="hotSrc">--</span></div><div class="metric"><span class="label">控制模式</span><b id="modeTop">自动</b><span class="muted" id="fresh">--</span></div></section><section class="main"><section class="panel"><div class="ph"><div><h2>风扇曲线</h2><p>绿色点为当前温度与实际输出；可在下表精确调节。</p></div><div class="seg"><button id="auto" class="on" onclick="setMode(false)">自动</button><button id="manual" onclick="setMode(true)">手动</button></div></div><div id="autoArea"><div class="curve"><svg id="svg" viewBox="0 0 760 245" preserveAspectRatio="none"></svg></div><div class="editor" id="rows"></div></div><div id="manualArea" class="manual"><span class="label">固定输出（手动模式将暂停自动曲线）</span><b><span id="mVal">40</span>%</b><input id="mSlide" style="width:100%;accent-color:#1769df" type="range" min="20" max="100" value="40" oninput="mChange()"></div><div class="save"><span id="dirty" class="muted">当前配置已同步</span><div><button class="btn secondary" onclick="discard()">放弃</button><button id="apply" class="btn primary" disabled onclick="apply()">确认应用</button></div></div></section><aside class="right"><section class="panel"><div class="ph"><div><h2>实时监测</h2><p>编辑曲线时持续观察硬件反馈。</p></div></div><div class="sensors"><div class="sensor"><span>CPU</span><b id="cpu">--°C</b></div><div class="sensor"><span>主板</span><b id="board">--°C</b></div><div class="sensor"><span>NVMe</span><b id="nvme">--°C</b></div><div class="sensor"><span>硬盘</span><b id="disk">--°C</b></div></div><div class="live"><span class="label">当前工作段</span><strong id="segment">等待曲线数据</strong></div><canvas class="mini" id="mini"></canvas></section><details class="diag"><summary>高级诊断 · 模块与硬件通道</summary><div><span id="module">--</span><span id="access">--</span><code id="path">--</code></div></details></aside></section><section class="panel history"><div class="ph"><div><h2>温度历史</h2><p>CPU、NVMe 与硬盘最近 50 条记录。</p></div></div><canvas id="hist"></canvas></section></main><div class="toast" id="toast"></div><script>let D,curve=[],dirty=false,manual=false,mp=40,logs=[];const $=x=>document.getElementById(x),p=v=>Math.round(v/255*100),n=v=>v==null?'--':Math.round(v);function toast(x){$('toast').textContent=x;$('toast').classList.add('on');setTimeout(()=>$('toast').classList.remove('on'),2300)}function draw(){let s=$('svg'),W=760,H=245,L=40,B=24,pts=curve.map(q=>[L+(q.temp-30)/55*(W-L-12),H-B-p(q.pwm)/100*(H-B-12)]),g='';[0,25,50,75,100].forEach(v=>{let y=H-B-v/100*(H-B-12);g+=`<line class="g" x1="${L}" x2="${W-12}" y1="${y}" y2="${y}"/><text class="axis" x="2" y="${y+4}">${v}%</text>`});[35,45,55,65,75,85].forEach(v=>{let x=L+(v-30)/55*(W-L-12);g+=`<text class="axis" x="${x-8}" y="${H-5}">${v}°</text>`});let path=pts.map((a,i)=>(i?'L':'M')+a.join(',')).join(' '),x=L+((D?.avg_temp||30)-30)/55*(W-L-12),y=H-B-p(D?.pwm||0)/100*(H-B-12);s.innerHTML=g+`<path class="line" d="${path}"/>`+pts.map(a=>`<circle class="pt" cx="${a[0]}" cy="${a[1]}" r="5"/>`).join('')+`<circle class="now" cx="${x}" cy="${y}" r="6"/>`}function render(){manual=manual;$('auto').classList.toggle('on',!manual);$('manual').classList.toggle('on',manual);$('autoArea').style.display=manual?'none':'block';$('manualArea').classList.toggle('show',manual);$('mSlide').value=mp;$('mVal').textContent=mp;if(!manual){draw();$('rows').innerHTML=curve.map((q,i)=>`<div class="erow"><span class="n">${i+1}</span><span>≤ ${q.temp}°C</span><b>${p(q.pwm)}%</b><input type="range" min="20" max="100" value="${p(q.pwm)}" oninput="change(${i},this.value)"></div>`).join('')}}function mark(){dirty=true;$('dirty').textContent='有未应用的更改';$('apply').disabled=false}function change(i,v){curve[i].pwm=Math.round(v*2.55);mark();render()}function setMode(v){manual=v;mark();render()}function mChange(){mp=+$('mSlide').value;$('mVal').textContent=mp;mark()}function discard(){dirty=false;if(D){curve=D.curve||[];manual=!!D.manual_mode;mp=p(D.manual_pwm||100);render()}$('dirty').textContent='当前配置已同步';$('apply').disabled=true}function apply(){let raw=Math.round(mp*2.55);$('apply').disabled=true;fetch('/api/curve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({curve,manual_mode:manual,manual_pwm:raw})}).then(r=>r.json()).then(x=>{if(!x.ok)throw 0;dirty=false;$('dirty').textContent='已应用并保存；重启后保留';toast('配置已应用');get()}).catch(()=>{$('apply').disabled=false;toast('应用失败')})}function paint(d){let b=Object.values(d.board_temps||{}),ds=Object.values(d.disk_temps||{}).filter(x=>typeof x==='number'),all=[d.cpu_temp,d.nvme0_temp,d.nvme1_temp,...b,...ds].filter(Number.isFinite),mx=Math.max(...all),src=mx===d.cpu_temp?'CPU':mx===d.nvme0_temp||mx===d.nvme1_temp?'NVMe':'主板/硬盘';$('avg').textContent=n(d.avg_temp)+'°C';$('source').textContent='当前由 '+src+' 与控制曲线决定';$('rpm').textContent=n(d.fan_rpm);$('out').textContent=p(d.pwm||0)+'%';$('raw').textContent='PWM '+(d.pwm||0)+' / 255';$('hot').textContent=n(mx)+'°C';$('hotSrc').textContent='来自 '+src;$('modeTop').textContent=d.manual_mode?'手动':'自动';$('fresh').textContent='更新 '+new Date().toLocaleTimeString();$('cpu').textContent=n(d.cpu_temp)+'°C';$('board').textContent=b.length?n(Math.max(...b))+'°C':'--°C';$('nvme').textContent=n(Math.max(d.nvme0_temp||0,d.nvme1_temp||0))+'°C';$('disk').textContent=ds.length?n(Math.max(...ds))+'°C':'--°C';$('module').textContent='模块：'+(d.system?.module_loaded?'已加载':'缺失');$('access').textContent='PWM：'+(d.system?.pwm_accessible?'可读写':'不可用');$('path').textContent=d.system?.hwmon_path||'--';let a=curve.findIndex(q=>(d.avg_temp||0)<=q.temp);$('segment').textContent=a<0?'已到最高曲线段':`≤ ${curve[a].temp}°C · 目标输出 ${p(curve[a].pwm)}%`}function get(){fetch('/api/status').then(r=>r.json()).then(d=>{D=d;if(!dirty){curve=d.curve||[];manual=!!d.manual_mode;mp=p(d.manual_pwm||100);render()}paint(d)}).catch(()=>{$('online').textContent='控制器不可达'})}function graph(id,short){let c=$(id),ctx=c.getContext('2d'),w=c.width=c.clientWidth*devicePixelRatio,h=c.height=c.clientHeight*devicePixelRatio;ctx.scale(devicePixelRatio,devicePixelRatio);w=c.clientWidth;h=c.clientHeight;ctx.clearRect(0,0,w,h);if(logs.length<2)return;let ss=[['#1769df',x=>x.cpu],['#0e9f6e',x=>x.disk?.max_temp]],vals=ss.flatMap(q=>logs.map(q[1])).filter(Number.isFinite),lo=Math.min(...vals)-2,hi=Math.max(...vals)+2;ss.forEach(([col,fn])=>{ctx.strokeStyle=col;ctx.lineWidth=2;ctx.beginPath();logs.forEach((d,i)=>{let v=fn(d);if(!Number.isFinite(v))return;let x=i/(logs.length-1)*w,y=h-10-(v-lo)/(hi-lo)*(h-20);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()})}function getLog(){fetch('/api/log').then(r=>r.json()).then(x=>{logs=x.log||[];graph('mini');graph('hist')})}get();getLog();setInterval(get,3000);setInterval(getLog,10000);addEventListener('resize',()=>{graph('mini');graph('hist')})</script></body></html>'''

if __name__ == '__main__':
    logger.info("Starting fnOS Fan Control v4.2")
    threading.Thread(target=control_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Web UI → http://0.0.0.0:{port}")
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
