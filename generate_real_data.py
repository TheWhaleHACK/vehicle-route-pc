#!/usr/bin/env python3
"""Download an OSM road route and SRTM heights; train an illustrative slope regressor.

Uses Python standard library only. The resulting CSVs and data.js are consumed by
index.html without a web server. This is a demonstration, not a driving controller.
"""
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data'
# City-centre starting points, snapped to the road by the routing service.
TORZHOK = (34.96221, 57.0436)  # lon, lat
BOLOGOYE = (34.078869, 57.878721)
STEP_M = 250.0
LOOKAHEAD = 2  # 500 m at 250 m sampling
UA = 'RouteGradeTeachingDemo/1.0 (one-off educational download)'


def fetch_json(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            if attempt == tries - 1:
                raise RuntimeError(f'Не удалось получить данные: {url.split("?")[0]}: {exc}') from exc
            time.sleep(2 ** attempt)


def haversine(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(x)))


def road_line():
    coords = f'{TORZHOK[0]},{TORZHOK[1]};{BOLOGOYE[0]},{BOLOGOYE[1]}'
    url = f'https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson'
    data = fetch_json(url)
    if data.get('code') != 'Ok' or not data.get('routes'):
        raise RuntimeError(f'OSRM не построил автомобильный маршрут: {data.get("code")}')
    route = data['routes'][0]
    line = [tuple(p[:2]) for p in route['geometry']['coordinates']]
    if len(line) < 10 or route['distance'] < 50000:
        raise RuntimeError('OSRM вернул слишком короткий или неполный маршрут')
    return line, route['distance']


def resample(line, step=STEP_M):
    cumulative = [0.0]
    for a, b in zip(line, line[1:]):
        cumulative.append(cumulative[-1] + haversine(a, b))
    result = []
    cursor = 0
    n = math.ceil(cumulative[-1] / step)
    for i in range(n + 1):
        dist = min(i * step, cumulative[-1])
        while cursor + 1 < len(cumulative) - 1 and cumulative[cursor + 1] < dist:
            cursor += 1
        span = cumulative[cursor + 1] - cumulative[cursor]
        t = (dist - cumulative[cursor]) / span if span else 0
        lon = line[cursor][0] + t * (line[cursor + 1][0] - line[cursor][0])
        lat = line[cursor][1] + t * (line[cursor + 1][1] - line[cursor][1])
        result.append({'distance_m': dist, 'longitude_deg': lon, 'latitude_deg': lat})
    return result


def elevations(points):
    heights = []
    for offset in range(0, len(points), 80):
        chunk = points[offset:offset + 80]
        locs = '|'.join(f'{p["latitude_deg"]:.6f},{p["longitude_deg"]:.6f}' for p in chunk)
        url = 'https://api.opentopodata.org/v1/srtm90m?' + urllib.parse.urlencode({'locations': locs, 'interpolation': 'bilinear'})
        result = fetch_json(url)
        if result.get('status') != 'OK' or len(result.get('results', [])) != len(chunk):
            raise RuntimeError('Ответ SRTM неполный или некорректный')
        part = [v.get('elevation') for v in result['results']]
        if any(v is None or not math.isfinite(float(v)) for v in part):
            raise RuntimeError('В выбранной области отсутствуют данные SRTM')
        heights.extend(float(v) for v in part)
        if offset + 80 < len(points):
            time.sleep(1.15)
    return heights


def smooth(values):
    weights = [1, 2, 3, 2, 1]
    return [sum(values[min(len(values) - 1, max(0, i + j - 2))] * w for j, w in enumerate(weights)) / 9 for i in range(len(values))]


def bearing(a, b):
    lat = math.radians((a['latitude_deg'] + b['latitude_deg']) / 2)
    dx = (b['longitude_deg'] - a['longitude_deg']) * math.cos(lat)
    dy = b['latitude_deg'] - a['latitude_deg']
    return math.atan2(dx, dy)


def grade_between(points, heights, i, j):
    return 100 * (heights[j] - heights[i]) / max(1, points[j]['distance_m'] - points[i]['distance_m'])


def features(points, heights, i):
    return [grade_between(points, heights, max(0, i - k), i) for k in (1, 2, 4)] + [heights[i] / 100]


def solve(matrix, rhs):
    a = [row[:] + [v] for row, v in zip(matrix, rhs)]
    n = len(rhs)
    for i in range(n):
        k = max(range(i, n), key=lambda r: abs(a[r][i]))
        a[i], a[k] = a[k], a[i]
        if abs(a[i][i]) < 1e-10:
            raise RuntimeError('Не удалось обучить модель: вырожденная матрица')
        div = a[i][i]
        a[i] = [x / div for x in a[i]]
        for j in range(n):
            if j == i:
                continue
            factor = a[j][i]
            a[j] = [x - factor * y for x, y in zip(a[j], a[i])]
    return [row[-1] for row in a]


def fit_ridge(x, y, penalty=5.0):
    means = [sum(col) / len(col) for col in zip(*x)]
    stds = [max(1e-6, math.sqrt(sum((row[j] - means[j]) ** 2 for row in x) / len(x))) for j in range(len(means))]
    z = [[1] + [(value - means[j]) / stds[j] for j, value in enumerate(row)] for row in x]
    n = len(z[0])
    gram = [[sum(row[i] * row[j] for row in z) + (penalty if i == j and i else 0) for j in range(n)] for i in range(n)]
    rhs = [sum(row[i] * value for row, value in zip(z, y)) for i in range(n)]
    return {'means': means, 'stds': stds, 'coefficients': solve(gram, rhs)}


def predict(model, row):
    return model['coefficients'][0] + sum(model['coefficients'][j + 1] * (value - model['means'][j]) / model['stds'][j] for j, value in enumerate(row))


def train(points, heights):
    valid = list(range(4, len(points) - LOOKAHEAD))
    if len(valid) < 50:
        raise RuntimeError('Недостаточно точек для проверки модели')
    xx = [features(points, heights, i) for i in valid]
    yy = [grade_between(points, heights, i, i + LOOKAHEAD) for i in valid]
    cut = int(len(valid) * .7)
    # Gap keeps the 500 m target interval of training away from the test set.
    train_end = max(30, cut - LOOKAHEAD)
    checked = fit_ridge(xx[:train_end], yy[:train_end])
    test_x, test_y = xx[cut:], yy[cut:]
    mae = sum(abs(predict(checked, x) - y) for x, y in zip(test_x, test_y)) / len(test_y)
    baseline = sum(abs(x[1] - y) for x, y in zip(test_x, test_y)) / len(test_y)
    final = fit_ridge(xx, yy)
    preds = [predict(final, features(points, heights, i)) for i in range(len(points))]
    return preds, {'holdout_mae_pct_points': round(mae, 3), 'baseline_mae_pct_points': round(baseline, 3), 'holdout_points': len(test_y), 'train_points': train_end, 'model': final}


def write_data(points, raw_heights, routing_distance):
    heights = smooth(raw_heights)
    preds, report = train(points, heights)
    OUT.mkdir(exist_ok=True)
    route_rows, vehicle_rows = [], []
    for i, p in enumerate(points):
        prev, nxt = max(0, i - 1), min(len(points) - 1, i + 1)
        grade = grade_between(points, heights, prev, nxt)
        angle = bearing(points[prev], points[nxt])
        if 0 < i < len(points) - 1:
            bend = abs((bearing(points[i], points[i + 1]) - bearing(points[i - 1], points[i]) + math.pi) % (2 * math.pi) - math.pi)
            radius = max(50, (points[i + 1]['distance_m'] - points[i - 1]['distance_m']) / (2 * max(bend, 1e-5)))
        else:
            radius = 99999
        # Simulated CAN baseline. The prediction adjusts the simulation; this is not road-speed advice.
        base_speed = min(75, 45 + 30 * min(1, p['distance_m'] / 1200, (points[-1]['distance_m'] - p['distance_m']) / 1200))
        forecast = preds[i]
        slope_cap = max(30, min(78, 76 - 5.0 * abs(forecast)))
        turn_cap = math.sqrt(1.5 * radius) * 3.6
        simulated = max(0, min(base_speed, slope_cap, turn_cap))
        route_rows.append({'point_id': i, 'distance_m': round(p['distance_m'], 2), 'latitude_deg': round(p['latitude_deg'], 7), 'longitude_deg': round(p['longitude_deg'], 7), 'elevation_m': round(heights[i], 2), 'grade_pct': round(grade, 3), 'heading_deg': round(math.degrees(angle) % 360, 2), 'curve_radius_m': round(radius, 1), 'predicted_grade_500m_pct': round(forecast, 3), 'raw_srtm_elevation_m': round(raw_heights[i], 2)})
        vehicle_rows.append({'point_id': i, 'distance_m': round(p['distance_m'], 2), 'speed_kmh': round(simulated, 2), 'base_speed_kmh': round(base_speed, 2), 'payload_kg': 6200, 'vehicle_mass_kg': 7800})
    for name, rows in [('route.csv', route_rows), ('vehicle.csv', vehicle_rows)]:
        with (OUT / name).open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    metadata = {'kind': 'real_osm_srtm', 'origin': 'Торжок', 'destination': 'Бологое', 'generated_utc': datetime.now(timezone.utc).isoformat(), 'road_source': 'OpenStreetMap road graph via OSRM demo', 'elevation_source': 'SRTM90m via Open Topo Data', 'distance_m': round(points[-1]['distance_m'], 1), 'osrm_distance_m': routing_distance, 'sample_step_m': STEP_M, 'forecast_horizon_m': 500, 'forecast': report, 'telemetry': 'simulated; not actual CAN readings'}
    (OUT / 'model_report.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'data.js').write_text('window.ROUTE_CSV=' + json.dumps((OUT / 'route.csv').read_text(encoding='utf-8'), ensure_ascii=False) + ';\nwindow.VEHICLE_CSV=' + json.dumps((OUT / 'vehicle.csv').read_text(encoding='utf-8'), ensure_ascii=False) + ';\nwindow.ROUTE_META=' + json.dumps(metadata, ensure_ascii=False) + ';\n', encoding='utf-8')
    return metadata


def main():
    print('Получаю дорожный маршрут Торжок → Бологое…', flush=True)
    line, routing_distance = road_line()
    points = resample(line)
    print(f'Точек: {len(points)}. Получаю высоты SRTM90m…', flush=True)
    heights = elevations(points)
    metadata = write_data(points, heights, routing_distance)
    print(f'Готово: {metadata["distance_m"] / 1000:.1f} км. Оценка MAE на отложенном участке: {metadata["forecast"]["holdout_mae_pct_points"]:.2f} процентного пункта. Базовый прогноз: {metadata["forecast"]["baseline_mae_pct_points"]:.2f}.')
    print('Откройте index.html. CSV и отчёт лежат в папке data.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        sys.exit(1)
