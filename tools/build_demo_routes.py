#!/usr/bin/env python3
"""Разовая подготовка маршрутов для демо-карты: по публичным данным OpenStreetMap (OSRM) строит дорожные маршруты от демо-точки кафе до тестовых
точек Алматы и записывает их в docs/data/demo-routes.json. Сайт при работе НЕ обращается к серверу маршрутизации — только читает этот файл,
поэтому не зависит от лимитов публичного OSRM. Запускать вручную и редко (несколько запросов).
Запуск из корня репозитория:  python3 tools/build_demo_routes.py"""
import json
import time
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "demo-routes.json"
UA = "bistro-demo/1.0 (one-off route preparation for a demo page)"
CAFE_REQ = (43.2450, 76.9300)   # демо-точка кафе в центре Алматы (условная, не реальный адрес кафе)
# Общественные места как тестовые точки: это не адреса клиентов
DESTS = [("republic-square", "Площадь Республики", 43.2388845, 76.9454739), ("dostyk-plaza", "Достык Плаза", 43.2334792, 76.9553339),
         ("botanical-garden", "Ботанический сад", 43.2193705, 76.9144766), ("central-park", "Центральный парк культуры и отдыха", 43.2616454, 76.9692738),
         ("first-president-park", "Парк Первого Президента", 43.1864167, 76.8867954)]

def get(url):
    # curl берёт системные сертификаты (у Python с python.org их по умолчанию нет); проверку TLS не отключаем
    out = subprocess.run(["curl", "-sS", "-m", "30", "-A", UA, url], capture_output=True, text=True, check=True).stdout
    return json.loads(out)

dests, cafe = [], None
for did, name, lat, lon in DESTS:
    d = get(f"https://router.project-osrm.org/route/v1/driving/{CAFE_REQ[1]},{CAFE_REQ[0]};{lon},{lat}?overview=full&geometries=geojson&steps=false")
    if d.get("code") != "Ok": raise SystemExit(f"OSRM: {did}: {d.get('code')}")
    r = d["routes"][0]; pts = [[round(y, 5), round(x, 5)] for x, y in r["geometry"]["coordinates"]]
    if cafe is None: cafe = pts[0]
    elif pts[0] != cafe: pts[0] = cafe
    dests.append({"id": did, "name": name, "note": "Тестовая точка, не адрес клиента", "lat": pts[-1][0], "lon": pts[-1][1],
                  "distanceM": round(r["distance"]), "durationS": round(r["duration"]), "route": pts})
    print(f"{did}: {len(pts)} точек, {round(r['distance'])} м, {round(r['duration'])} с")
    time.sleep(1.5)
data = {"version": 1, "generated": date.today().isoformat(),
        "source": "Маршруты рассчитаны заранее по данным OpenStreetMap (OSRM); значения расстояния и времени демонстрационные.",
        "map": {"center": [43.2400, 76.9400], "zoom": 13, "minZoom": 11, "maxZoom": 18, "tileUrl": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": "© участники OpenStreetMap", "attributionUrl": "https://www.openstreetmap.org/copyright"},
        "cafe": {"name": "Кафе «Бистро» (демо-точка)", "lat": cafe[0], "lon": cafe[1]}, "destinations": dests}
OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print("записано:", OUT.relative_to(ROOT), OUT.stat().st_size // 1024, "КБ")
