#!/usr/bin/env python3
"""Браузерные тесты демо-карты (этап 8) в настоящем WebKit на 390, 768 и 1280 px: карта Алматы с тайлами OpenStreetMap, дорожный маршрут,
виртуальный курьер едет именно по линии маршрута, запуск/пауза/продолжение/сброс/повтор, смена тестовой точки, изоляция от заказов, склада и
хранилища, обработка ошибок (нет маршрутов, нет подложки, нет библиотеки). Тайлы реально грузятся из сети; если сервер карты недоступен
с этой машины, проверка загрузки тайлов пропускается с предупреждением (остальное проверяется).
Запуск:  python3 tools/browser_map_tests.py [--shots ПАПКА] [--quick] [--root КОПИЯ] [--url АДРЕС_САЙТА]    Все данные выдуманные."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pages_server  # noqa: E402

args = sys.argv[1:]
SHOTS = Path(args[args.index("--shots") + 1]) if "--shots" in args else None
QUICK = "--quick" in args
LIVE = args[args.index("--url") + 1].rstrip("/") + "/" if "--url" in args else None
if sys.platform != "darwin" or not shutil.which("swiftc"):
    print("ПРЕДУПРЕЖДЕНИЕ: нужны macOS и swiftc — браузерные тесты карты пропущены"); sys.exit(0)
TMP = Path(tempfile.mkdtemp()); BIN = TMP / "webkit_run"
subprocess.run(["swiftc", "-O", str(Path(__file__).resolve().parent / "webkit_run.swift"), "-o", str(BIN)], capture_output=True, text=True)
if not BIN.exists(): print("ОШИБКА: не удалось собрать раннер WebKit"); sys.exit(1)
if SHOTS: SHOTS.mkdir(parents=True, exist_ok=True)
srv, BASE = (None, LIVE) if LIVE else pages_server.start(ROOT)
errors, checks, warnings = [], 0, []
def expect(cond, msg):
    global checks
    checks += 1
    if not cond: errors.append(msg)

# --- сервер карты доступен с этой машины? (иначе загрузку тайлов не проверяем) ---
TILES_OK = subprocess.run(["curl", "-s", "-m", "10", "-o", "/dev/null", "-w", "%{http_code}", "-A", "bistro-demo-test/1.0", "https://tile.openstreetmap.org/13/5934/2992.png"], capture_output=True, text=True).stdout == "200"
if not TILES_OK: warnings.append("сервер тайлов недоступен с этой машины — загрузка подложки не проверялась")

SETUP = r"""
const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)], wait = (ms) => new Promise(r => setTimeout(r, ms));
const txt = (el) => el ? el.textContent.replace(/\s+/g, ' ').trim() : null;
const vis = (el) => !!el && !el.hidden && getComputedStyle(el).display !== 'none';
const overflow = () => document.documentElement.scrollWidth > window.innerWidth + 1;
const ctr = (el) => { const r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const pinPos = (cls) => { const e = q('.map-pin--' + cls + ' > span'); return e ? ctr(e) : null; };
const status = () => txt(q('#st'));
const until = async (fn, ms = 20000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await wait(80); } return false; };
// точки линии маршрута в координатах окна: по ним проверяется, что курьер едет по дороге
const roadPts = () => { const p = q('.leaflet-overlay-pane path'), svg = p.ownerSVGElement, m = p.getScreenCTM(), n = p.getTotalLength(), out = []; for (let i = 0; i <= 600; i++) { const pt = svg.createSVGPoint(), a = p.getPointAtLength(n * i / 600); pt.x = a.x; pt.y = a.y; const s = pt.matrixTransform(m); out.push([s.x, s.y]); } return out; };
const offRoad = (pts, c) => Math.min(...pts.map(p => dist(p, c)));
const STORE = () => JSON.stringify(Object.keys(localStorage).sort().map(k => [k, localStorage.getItem(k)]));
"""
def js(b): return SETUP + b

def plan_for(width, height, tag):
    ph, names = [], {}
    shot = (lambda n: str(SHOTS / f"map_{n}_{tag}.png")) if SHOTS else (lambda n: None)
    def phase(name, url, waitfor, script, shotname=None):
        d = {"waitFor": waitfor, "script": js(script), "timeout": 15}
        if url: d["url"] = BASE + url
        if shotname and SHOTS: d.update(screenshot=shot(shotname), full=True)
        names[name] = len(ph); ph.append(d)
    # изоляция: в хранилище уже лежат «настоящие» данные; демо не должно их менять
    phase("seed", "index.html", ".cat-tile", r"""
      localStorage.setItem('bistro.orders.v1', '[{"keep":1}]'); localStorage.setItem('bistro.cart.v1', '{"items":[{"id":"fries","qty":2}],"comment":"x"}');
      localStorage.setItem('bistro.menu.availability.v1', '{"fries":false}'); localStorage.setItem('bistro.admin.session.v1', '{"demoAdmin":true}'); localStorage.setItem('test.before', STORE()); return 'ok';""")
    phase("load", "track-demo.html?speed=6", ".map-pin--courier", r"""
      await wait(2500); const out = { h1: txt(q('h1')), note: txt(q('#demo-note')), banner: txt(q('#demo-banner')), tag: vis(q('.map-demo-tag')) ? txt(q('.map-demo-tag')) : '', order: txt(q('[data-demo-order]')), courier: txt(q('[data-courier]')), overflow: overflow() };
      const map = q('#map').getBoundingClientRect(); out.map = { w: Math.round(map.width), h: Math.round(map.height), vw: innerWidth };
      const tiles = qa('img.leaflet-tile'); out.tiles = { n: tiles.length, loaded: tiles.filter(t => t.complete && t.naturalWidth > 0).length, urls: tiles.slice(0, 40).map(t => t.getAttribute('src')) };
      out.attr = txt(q('.leaflet-control-attribution')); out.attrLink = qa('.leaflet-control-attribution a').find(a => a.textContent.includes('OpenStreetMap'))?.getAttribute('rel');
      const path = q('.leaflet-overlay-pane path'); out.path = { segs: (path.getAttribute('d').match(/L/g) || []).length, stroke: path.getAttribute('stroke') };
      out.pins = { cafe: !!pinPos('cafe'), dest: !!pinPos('dest'), courier: !!pinPos('courier'), points: qa('.map-pin--point').length };
      out.cafeCourier = Math.round(dist(pinPos('cafe'), pinPos('courier'))); out.cafeDest = Math.round(dist(pinPos('cafe'), pinPos('dest')));
      out.inMap = ['cafe', 'dest', 'courier'].map(c => { const p = pinPos(c), r = q('#map').getBoundingClientRect(); return p[0] > r.left && p[0] < r.right && p[1] > r.top && p[1] < r.bottom; });
      out.ctl = { start: vis(q('#b-start')), pause: vis(q('#b-pause')), resume: vis(q('#b-resume')), reset: !q('#b-reset').disabled, startText: txt(q('#b-start')) };
      out.st = status(); out.eta = txt(q('#eta')); out.dist = txt(q('#dist')); out.options = qa('#dest option').length; out.btnH = Math.round(q('#b-start').getBoundingClientRect().height);
      out.stages = qa('.stages li').map(li => li.className); out.hosts = [...new Set(performance.getEntriesByType('resource').map(r => new URL(r.name).host))];
      return JSON.stringify(out);""", "idle")
    # поездка до дальней точки (маршрут с изгибами): проверка этапов и движения по дороге
    phase("run", None, "#dest", r"""
      setv = (v) => { const s = q('#dest'); s.value = v; s.dispatchEvent(new Event('change', { bubbles: true })); };
      setv('first-president-park'); await wait(500); const pts0 = roadPts(), out = { distTxt: txt(q('#dist')) };
      q('#b-start').click(); await wait(100);
      out.afterStart = { start: vis(q('#b-start')), pause: vis(q('#b-pause')), resume: vis(q('#b-resume')), reset: !q('#b-reset').disabled, st: status() };
      const seen = [], samples = []; let maxOff = 0, t0 = Date.now();
      while (Date.now() - t0 < 30000) {
        const s = status(); if (seen[seen.length - 1] !== s) seen.push(s);
        const c = pinPos('courier'); const pts = roadPts(); const off = offRoad(pts, c); maxOff = Math.max(maxOff, off); samples.push([Math.round(c[0]), Math.round(c[1])]);
        if (s.includes('Заказ доставлен')) break; await wait(150);
      }
      out.seen = seen; out.maxOff = Math.round(maxOff * 10) / 10; out.moved = Math.round(dist(samples[0], samples[samples.length - 1]));
      const c = pinPos('courier'), d = pinPos('dest'); out.finalToDest = Math.round(dist(c, d)); out.finalBtn = { start: txt(q('#b-start')), startVis: vis(q('#b-start')), pause: vis(q('#b-pause')) };
      out.eta = txt(q('#eta')); out.done = qa('.stages li').map(li => li.className); out.cur = qa('.stages li[aria-current]').length;
      // прямая линия между кафе и клиентом: курьер на ней не ездит
      const a = pts0[0], b = pts0[pts0.length - 1]; out.roadBulge = Math.round(Math.max(...pts0.map(p => Math.abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / Math.hypot(b[0] - a[0], b[1] - a[1]))));
      return JSON.stringify(out);""", "done")
    # пауза, продолжение, сброс
    phase("pause", None, "#b-start", r"""
      q('#b-reset').click(); await wait(200); const out = {};
      out.afterReset = { st: status(), start: vis(q('#b-start')), pause: vis(q('#b-pause')), reset: !q('#b-reset').disabled, atCafe: Math.round(dist(pinPos('courier'), pinPos('cafe'))), stages: qa('.stages li').map(li => li.className).join('') };
      q('#b-start').click(); out.reached = await until(() => status() === 'Курьер в пути'); const a = pinPos('courier');
      q('#b-pause').click(); await wait(100); const p1 = pinPos('courier'); out.pausedUi = { st: status(), resume: vis(q('#b-resume')), pause: vis(q('#b-pause')), start: vis(q('#b-start')) };
      await wait(900); const p2 = pinPos('courier'); out.frozen = Math.round(dist(p1, p2) * 10) / 10; out.etaFrozen = txt(q('#eta'));
      q('#b-resume').click(); await wait(900); const p3 = pinPos('courier'); out.resumed = { moved: Math.round(dist(p2, p3)), st: status(), pause: vis(q('#b-pause')), resume: vis(q('#b-resume')) };
      out.offRoad = Math.round(offRoad(roadPts(), p3) * 10) / 10;
      q('#b-reset').click(); await wait(200); out.resetAgain = { atCafe: Math.round(dist(pinPos('courier'), pinPos('cafe'))), st: status() };
      return JSON.stringify(out);""", "paused")
    # повторный запуск после доставки + смена точки + выбор точки на карте
    phase("restart", None, "#b-start", r"""
      const out = {}; q('#b-start').click(); out.done = await until(() => status().includes('Заказ доставлен'), 30000); out.startText = txt(q('#b-start'));
      q('#b-start').click(); await wait(200); out.again = { st: status(), atCafe: Math.round(dist(pinPos('courier'), pinPos('cafe'))), pause: vis(q('#b-pause')) };
      q('#b-reset').click(); await wait(100);
      const before = { dist: txt(q('#dist')), dest: pinPos('dest').map(Math.round), path: q('.leaflet-overlay-pane path').getAttribute('d').length };
      const s = q('#dest'); s.value = 'republic-square'; s.dispatchEvent(new Event('change', { bubbles: true })); await wait(700);
      out.switched = { distBefore: before.dist, distAfter: txt(q('#dist')), moved: Math.round(dist(before.dest, pinPos('dest'))) > 5, pathChanged: q('.leaflet-overlay-pane path').getAttribute('d').length !== before.path, st: status(), atCafe: Math.round(dist(pinPos('courier'), pinPos('cafe'))) };
      s.value = 'first-president-park'; s.dispatchEvent(new Event('change', { bubbles: true })); await wait(700);
      const pt = qa('.map-pin--point > span').find(e => txt(e).includes('Достык')); const hit = document.elementFromPoint(...ctr(pt)); out.hit = hit === pt || pt.contains(hit); hit && hit.click(); await wait(700); out.picked = { value: q('#dest').value, text: txt(q('#dist')) };
      out.overflow = overflow(); return JSON.stringify(out);""")
    phase("mid", None, "#b-start", r"""
      q('#b-reset').click(); await wait(100); q('#b-start').click(); await until(() => status() === 'Курьер в пути'); await wait(1200); return JSON.stringify({ st: status() });""", "running")
    # изоляция: демо ничего не записало и не изменило
    phase("isolation", None, "#b-reset", r"""
      const before = localStorage.getItem('test.before'), now = (() => { const o = {}; Object.keys(localStorage).filter(k => k !== 'test.before').forEach(k => o[k] = localStorage.getItem(k)); return o; })();
      const keep = JSON.parse(before).filter(([k]) => k !== 'test.before');
      return JSON.stringify({ same: JSON.stringify(keep) === JSON.stringify(Object.keys(now).sort().map(k => [k, now[k]])), keys: Object.keys(now).sort(), session: sessionStorage.length, hosts: [...new Set(performance.getEntriesByType('resource').map(r => new URL(r.name).host))] });""")
    return {"width": width, "height": height, "phases": ph}, names

def run_plan(plan, tag):
    f = TMP / f"plan_{tag}.json"; f.write_text(json.dumps(plan), encoding="utf-8")
    try: res = subprocess.run([str(BIN), str(f)], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", str(BIN)]); errors.append(f"[{tag}] тайм-аут запуска WebKit"); return None
    try: return json.loads(res.stdout)
    except Exception: errors.append(f"[{tag}] раннер не вернул JSON: {(res.stdout + res.stderr)[:300]}"); return None

def getter(out, names, tag):
    def get(name):
        ph = out["phases"][names[name]]
        if ph.get("error") or ph.get("waited") is False: errors.append(f"[{tag}] фаза «{name}»: {ph.get('error')}"); return None
        try: return json.loads(ph["result"].replace(" ", " "))
        except Exception: return ph.get("result")
    return get

STAGES7 = ["Заказ принят", "Заказ готовится", "Заказ готов", "Курьер забрал заказ", "Курьер в пути", "Курьер почти у клиента", "Заказ доставлен (демо)"]
def check_viewport(width, height, tag):
    plan, names = plan_for(width, height, tag); out = run_plan(plan, tag)
    if not out: return
    get = getter(out, names, tag); E = lambda c, m: expect(c, f"[{tag}] {m}")
    r = get("load")
    if r:
        E(r["h1"] == "Демо-отслеживание доставки" and "ДЕМО-РЕЖИМ" in r["note"] and "GPS не используется" in r["note"] and r["tag"] == "ДЕМО-РЕЖИМ" and r["banner"].startswith("Демонстрационная версия"), f"метки демо-режима: {r['h1']} / {r['note'][:60]} / {r['tag']}")
        E(r["order"] == "DEMO-0001" and "виртуальный" in r["courier"] and not r["overflow"], f"демо-заказ и курьер: {r['order']} / {r['courier']} / прокрутка {r['overflow']}")
        E(r["map"]["w"] >= min(r["map"]["vw"] - 40, 300) and r["map"]["h"] >= 300, f"размер карты {r['map']}")
        # тайлы: адреса принадлежат Алматы (центр 43.24 N, 76.94 E) на нужном масштабе
        urls = [u for u in r["tiles"]["urls"] if u]
        E(len(urls) > 0 and all(re.match(r"https://tile\.openstreetmap\.org/\d+/\d+/\d+\.png$", u) for u in urls), f"адреса тайлов: {urls[:3]}")
        zs = {int(u.split("/")[-3]) for u in urls}; z = max(zs) if zs else 13
        cx, cy = int((76.94 + 180) / 360 * 2 ** z), int(__import__("math").floor((1 - __import__("math").log(__import__("math").tan(__import__("math").radians(43.24)) + 1 / __import__("math").cos(__import__("math").radians(43.24))) / __import__("math").pi) / 2 * 2 ** z))
        near = [u for u in urls if int(u.split("/")[-3]) == z and abs(int(u.split("/")[-2]) - cx) <= 8 and abs(int(u.split("/")[-1].split(".")[0]) - cy) <= 8]
        E(len(near) == len([u for u in urls if int(u.split("/")[-3]) == z]) and len(near) > 0, f"тайлы не в районе Алматы (z={z}, ожидается x≈{cx}, y≈{cy}): {urls[:3]}")
        if TILES_OK: E(r["tiles"]["loaded"] >= 3, f"картинки подложки реально загрузились: {r['tiles']['loaded']} из {r['tiles']['n']}")
        E(r["attr"] and "OpenStreetMap" in r["attr"] and r["attrLink"] and "noopener" in r["attrLink"], f"указание авторства карты: {r['attr']}")
        E(r["path"]["segs"] >= 8 and r["path"]["stroke"], f"маршрут нарисован ломаной из {r['path']['segs']} отрезков (прямая линия дала бы 1)")
        E(r["pins"] == {"cafe": True, "dest": True, "courier": True, "points": 5} and all(r["inMap"]) and r["cafeDest"] > 40 and r["cafeCourier"] <= 3, f"метки: {r['pins']} в карте {r['inMap']} кафе–клиент {r['cafeDest']}px курьер у кафе {r['cafeCourier']}px")
        E(r["ctl"] == {"start": True, "pause": False, "resume": False, "reset": False, "startText": "Запустить"} and r["st"] == "Демонстрация не запущена" and r["options"] == 5, f"исходное состояние: {r['ctl']} / {r['st']}")
        E("(демо)" in r["eta"] and "км" in r["dist"] and r["btnH"] >= 40 and set(r["stages"]) == {""}, f"время/расстояние/кнопки: {r['eta']} / {r['dist']} / высота кнопки {r['btnH']}")
        E(set(r["hosts"]) <= {BASE.split("/")[2], "tile.openstreetmap.org"}, f"обращения к посторонним серверам: {r['hosts']}")
    r = get("run")
    if r:
        E(r["afterStart"] == {"start": False, "pause": True, "resume": False, "reset": True, "st": r["afterStart"]["st"]} and r["afterStart"]["st"] in ("Заказ принят", "Заказ готовится"), f"после запуска: {r['afterStart']}")
        E(r["seen"] == STAGES7, f"этапы демонстрации по порядку: {r['seen']}")
        E(r["maxOff"] <= 4, f"курьер уходил с линии маршрута на {r['maxOff']}px")
        E(r["roadBulge"] > 6, f"маршрут почти прямой ({r['roadBulge']}px) — проверка «не по прямой» была бы бессмысленной")
        E(r["moved"] > 60 and r["finalToDest"] <= 4, f"курьер доехал: смещение {r['moved']}px, до клиента {r['finalToDest']}px")
        E(r["finalBtn"] == {"start": "Запустить снова", "startVis": True, "pause": False} and r["eta"] == "доставлено" and set(r["done"][:6]) == {"stage--done"} and r["done"][6] == "stage--current" and r["cur"] == 1, f"итог: {r['finalBtn']} / {r['eta']} / {r['done']}")
    r = get("pause")
    if r:
        a = r["afterReset"]; E(a["st"] == "Демонстрация не запущена" and a["start"] and not a["pause"] and not a["reset"] and a["atCafe"] <= 3 and a["stages"] == "", f"сброс: {a}")
        E(r["reached"] and r["pausedUi"] == {"st": "Пауза: Курьер в пути", "resume": True, "pause": False, "start": False}, f"пауза: {r['pausedUi']}")
        E(r["frozen"] <= 0.5, f"на паузе курьер сдвинулся на {r['frozen']}px")
        E(r["resumed"]["moved"] > 3 and r["resumed"]["st"] != "Пауза: Курьер в пути" and r["resumed"]["pause"] and not r["resumed"]["resume"] and r["offRoad"] <= 4, f"продолжение: {r['resumed']} вне дороги {r['offRoad']}px")
        E(r["resetAgain"]["atCafe"] <= 3 and r["resetAgain"]["st"] == "Демонстрация не запущена", f"сброс в пути: {r['resetAgain']}")
    r = get("restart")
    if r:
        E(r["done"] and r["startText"] == "Запустить снова" and r["again"]["st"] in ("Заказ принят", "Заказ готовится") and r["again"]["atCafe"] <= 3 and r["again"]["pause"], f"повторный запуск: {r['again']}")
        s = r["switched"]; E(s["distBefore"] != s["distAfter"] and s["moved"] and s["pathChanged"] and s["st"] == "Демонстрация не запущена" and s["atCafe"] <= 3, f"смена точки: {s}")
        E(r["hit"] and r["picked"]["value"] == "dostyk-plaza" and "4,7" in r["picked"]["text"] and not r["overflow"], f"выбор точки на карте: {r['picked']}")
    r = get("mid")
    if r: E(r["st"] in ("Курьер в пути", "Курьер почти у клиента"), f"снимок в пути: {r}")
    r = get("isolation")
    if r: E(r["same"] and r["session"] == 0 and set(r["hosts"]) <= {BASE.split("/")[2], "tile.openstreetmap.org"}, f"изоляция: хранилище изменено демо {r}")
    for c in out.get("console", []):
        if c.startswith(("error|", "rejection|", "console.error|")) and "tile.openstreetmap" not in c: errors.append(f"[{tag}] ошибка JavaScript: {c[:200]}")

def check_failures():
    """Ошибки: нет файла маршрутов, нет подложки (недоступный сервер тайлов), нет библиотеки карты."""
    variants = {"routes": ("data/demo-routes.json", "{"), "leaflet": ("assets/vendor/leaflet/leaflet.js", ""), "tiles": None}
    for name, spec in variants.items():
        base = TMP / f"copy_{name}"; shutil.copytree(ROOT / "docs", base / "docs")
        if spec: (base / "docs" / spec[0]).write_text(spec[1], encoding="utf-8")
        else:
            f = base / "docs" / "data" / "demo-routes.json"; d = json.loads(f.read_text(encoding="utf-8")); d["map"]["tileUrl"] = "http://127.0.0.1:9/{z}/{x}/{y}.png"; f.write_text(json.dumps(d), encoding="utf-8")
        s2, b2 = pages_server.start(base)
        ph = {"waitFor": "#track-error" if spec else ".map-pin--courier", "timeout": 15, "url": b2 + "track-demo.html?speed=20", "script": js(r"""
          await wait(3500); const out = { error: vis(q('#track-error')), errText: txt(q('#track-error')), retry: !!q('[data-retry]'), note: txt(q('#map-note')) || '', demo: !!q('.map-demo-tag'), overflow: overflow() };
          if (!out.error) { q('#b-start').click(); out.moved = await until(() => status().includes('Заказ доставлен'), 20000); out.path = !!q('.leaflet-overlay-pane path'); }
          return JSON.stringify(out);""")}
        out = run_plan({"width": 390, "height": 800, "phases": [ph]}, "fail_" + name); s2.shutdown()
        if not out: continue
        p = out["phases"][0]
        if p.get("error"): errors.append(f"[ошибки:{name}] {p['error']}"); continue
        r = json.loads(p["result"]); E = lambda c, m: expect(c, f"[ошибки:{name}] {m}")
        if name in ("routes", "leaflet"): E(r["error"] and "Не удалось загрузить демонстрацию" in r["errText"] and "Реальные заказы это не затрагивает" in r["errText"] and r["retry"] and not r["overflow"], f"сообщение об ошибке: {r}")
        else: E("Подложка карты не загрузилась" in r["note"] and r["moved"] and r["path"] and r["demo"], f"без подложки демонстрация работает и предупреждает: {r}")
        for c in out.get("console", []):
            if c.startswith(("error|", "rejection|")) and name == "tiles" and False: errors.append(c)

def check_static():
    """Исходный код демо не должен писать в хранилище, обращаться к сети или к серверам заказов/склада."""
    docs = ROOT / "docs"; pure = (docs / "assets/js/courier-demo.js").read_text(encoding="utf-8"); page = (docs / "assets/js/pages/track-demo.js").read_text(encoding="utf-8")
    E = lambda c, m: expect(c, f"[код] {m}")
    E(not re.search(r"localStorage|sessionStorage|fetch\(|XMLHttpRequest|document\.|window\.|indexedDB", pure), "логика курьера обращается к хранилищу, сети или DOM")
    E(not re.search(r"localStorage|sessionStorage|indexedDB|writeJSON|saveOrders|saveCart|updateOrder|saveOverrides|createRealProvider|fetch\(", page), "страница демо пишет в хранилище или в сеть")
    imports = re.findall(r'from\s+"([^"]+)"', page)
    E(not any(re.search(r"orders-store|orders-logic|admin-store|cart-store|cart-logic|supabase", i) for i in imports), f"страница демо импортирует модули заказов/склада: {imports}")
    E(not [p for p in docs.rglob("*") if p.is_file() and p.suffix in {".js", ".html", ".json", ".css"} and re.search("supabase|service" + "_role", p.read_text(encoding="utf-8", errors="ignore"), re.I)], "в сайте встречаются упоминания серверной части (supabase и ключа сервисной роли)")
    E(bool(re.search(r"Реальный GPS курьера не подключён", pure)), "нет явной заглушки реального провайдера")

try:
    check_static()
    check_viewport(390, 800, "phone")
    if not QUICK:
        check_viewport(768, 1000, "tablet"); check_viewport(1280, 900, "desktop"); check_failures()
except (KeyError, TypeError, IndexError, AttributeError) as e:
    errors.append(f"сценарий вернул неполные данные: {type(e).__name__}: {e}")
if srv: srv.shutdown()
print(f"Браузерные проверки карты (WebKit): {checks}" + (f"; скриншоты: {SHOTS}" if SHOTS else ""))
for w in warnings: print("ПРЕДУПРЕЖДЕНИЕ:", w)
for e in errors: print("ОШИБКА:", e)
print("ИТОГ:", "браузерные тесты карты пройдены" if not errors else f"ошибок: {len(errors)}")
sys.exit(1 if errors else 0)
