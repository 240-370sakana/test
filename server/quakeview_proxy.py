#!/usr/bin/env python3
"""
QuakeView 統合サーバー
ETAS解析 + DB API + 強震モニタ(リアルタイム震度) 統合版
"""

import datetime
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import urllib.request, urllib.error, ssl, os, sys, threading, webbrowser
import mimetypes, json as _json, configparser, subprocess, tempfile
import xml.etree.ElementTree as ET
import time
import socket
import io
import csv
import logging
import re
from pathlib import Path

# 追加ライブラリのチェック
try:
    import numpy as np
    from PIL import Image
    import requests
except ImportError:
    print("エラー: 必要なライブラリが不足しています。以下を実行してください:")
    print("pip install numpy pillow requests")
    sys.exit(1)

PORT = 8765

# パス設定
SERVER_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SERVER_DIR, '..'))
STATIONS_CSV = os.path.join(PROJECT_ROOT, "Downloads", "stations.csv")

# SSL 証明書検証スキップ
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode    = ssl.CERT_NONE

# MIME 補完
EXTRA_MIME = {
    '.html':    'text/html; charset=utf-8',
    '.css':     'text/css; charset=utf-8',
    '.js':      'application/javascript; charset=utf-8',
    '.json':    'application/json; charset=utf-8',
    '.csv':     'text/csv; charset=utf-8',
    '.geojson': 'application/geo+json; charset=utf-8',
    '.svg':     'image/svg+xml',
    '.png':     'image/png',
    '.jpg':     'image/jpeg',
    '.ico':     'image/x-icon',
}

def get_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return EXTRA_MIME.get(ext) or mimetypes.guess_type(path)[0] or 'application/octet-stream'

# =============================================================================
#  強震モニタ（リアルタイム震度）解析ロジック
# =============================================================================
BASE_URL      = "https://smi.lmoniexp.bosai.go.jp"
LATEST_URL    = f"{BASE_URL}/webservice/server/pros/latest.json"
GIF_BASE_URL  = f"{BASE_URL}/data/map_img/RealTimeImg/jma_s"
HEADERS       = {"Referer": f"{BASE_URL}/"}
POLL_INTERVAL = 1.0

_COLORMAP_JSON = '[{"Intensity":-3,"R":0,"G":0,"B":205},{"Intensity":-2.9,"R":0,"G":7,"B":209},{"Intensity":-2.8,"R":0,"G":14,"B":214},{"Intensity":-2.7,"R":0,"G":21,"B":218},{"Intensity":-2.6,"R":0,"G":28,"B":223},{"Intensity":-2.5,"R":0,"G":36,"B":227},{"Intensity":-2.4,"R":0,"G":43,"B":231},{"Intensity":-2.3,"R":0,"G":50,"B":236},{"Intensity":-2.2,"R":0,"G":57,"B":240},{"Intensity":-2.1,"R":0,"G":64,"B":245},{"Intensity":-2,"R":0,"G":72,"B":250},{"Intensity":-1.9,"R":0,"G":85,"B":238},{"Intensity":-1.8,"R":0,"G":99,"B":227},{"Intensity":-1.7,"R":0,"G":112,"B":216},{"Intensity":-1.6,"R":0,"G":126,"B":205},{"Intensity":-1.5,"R":0,"G":140,"B":194},{"Intensity":-1.4,"R":0,"G":153,"B":183},{"Intensity":-1.3,"R":0,"G":167,"B":172},{"Intensity":-1.2,"R":0,"G":180,"B":161},{"Intensity":-1.1,"R":0,"G":194,"B":150},{"Intensity":-1,"R":0,"G":208,"B":139},{"Intensity":-0.9,"R":6,"G":212,"B":130},{"Intensity":-0.8,"R":12,"G":216,"B":121},{"Intensity":-0.7,"R":18,"G":220,"B":113},{"Intensity":-0.6,"R":25,"G":224,"B":104},{"Intensity":-0.5,"R":31,"G":228,"B":96},{"Intensity":-0.4,"R":37,"G":233,"B":88},{"Intensity":-0.3,"R":44,"G":237,"B":79},{"Intensity":-0.2,"R":50,"G":241,"B":71},{"Intensity":-0.1,"R":56,"G":245,"B":62},{"Intensity":0,"R":63,"G":250,"B":54},{"Intensity":0.1,"R":75,"G":250,"B":49},{"Intensity":0.2,"R":88,"G":250,"B":45},{"Intensity":0.3,"R":100,"G":251,"B":41},{"Intensity":0.4,"R":113,"G":251,"B":37},{"Intensity":0.5,"R":125,"G":252,"B":33},{"Intensity":0.6,"R":138,"G":252,"B":28},{"Intensity":0.7,"R":151,"G":253,"B":24},{"Intensity":0.8,"R":163,"G":253,"B":20},{"Intensity":0.9,"R":176,"G":254,"B":16},{"Intensity":1,"R":189,"G":255,"B":12},{"Intensity":1.1,"R":195,"G":254,"B":10},{"Intensity":1.2,"R":202,"G":254,"B":9},{"Intensity":1.3,"R":208,"G":254,"B":8},{"Intensity":1.4,"R":215,"G":254,"B":7},{"Intensity":1.5,"R":222,"G":255,"B":5},{"Intensity":1.6,"R":228,"G":254,"B":4},{"Intensity":1.7,"R":235,"G":255,"B":3},{"Intensity":1.8,"R":241,"G":254,"B":2},{"Intensity":1.9,"R":248,"G":255,"B":1},{"Intensity":2,"R":255,"G":255,"B":0},{"Intensity":2.1,"R":254,"G":251,"B":0},{"Intensity":2.2,"R":254,"G":248,"B":0},{"Intensity":2.3,"R":254,"G":244,"B":0},{"Intensity":2.4,"R":254,"G":241,"B":0},{"Intensity":2.5,"R":255,"G":238,"B":0},{"Intensity":2.6,"R":254,"G":234,"B":0},{"Intensity":2.7,"R":255,"G":231,"B":0},{"Intensity":2.8,"R":254,"G":227,"B":0},{"Intensity":2.9,"R":255,"G":224,"B":0},{"Intensity":3,"R":255,"G":221,"B":0},{"Intensity":3.1,"R":254,"G":213,"B":0},{"Intensity":3.2,"R":254,"G":205,"B":0},{"Intensity":3.3,"R":254,"G":197,"B":0},{"Intensity":3.4,"R":254,"G":190,"B":0},{"Intensity":3.5,"R":255,"G":182,"B":0},{"Intensity":3.6,"R":254,"G":174,"B":0},{"Intensity":3.7,"R":255,"G":167,"B":0},{"Intensity":3.8,"R":254,"G":159,"B":0},{"Intensity":3.9,"R":255,"G":151,"B":0},{"Intensity":4,"R":255,"G":144,"B":0},{"Intensity":4.1,"R":254,"G":136,"B":0},{"Intensity":4.2,"R":254,"G":128,"B":0},{"Intensity":4.3,"R":254,"G":121,"B":0},{"Intensity":4.4,"R":254,"G":113,"B":0},{"Intensity":4.5,"R":255,"G":106,"B":0},{"Intensity":4.6,"R":254,"G":98,"B":0},{"Intensity":4.7,"R":255,"G":90,"B":0},{"Intensity":4.8,"R":254,"G":83,"B":0},{"Intensity":4.9,"R":255,"G":75,"B":0},{"Intensity":5,"R":255,"G":68,"B":0},{"Intensity":5.1,"R":254,"G":61,"B":0},{"Intensity":5.2,"R":253,"G":54,"B":0},{"Intensity":5.3,"R":252,"G":47,"B":0},{"Intensity":5.4,"R":251,"G":40,"B":0},{"Intensity":5.5,"R":250,"G":33,"B":0},{"Intensity":5.6,"R":249,"G":27,"B":0},{"Intensity":5.7,"R":248,"G":20,"B":0},{"Intensity":5.8,"R":247,"G":13,"B":0},{"Intensity":5.9,"R":246,"G":6,"B":0},{"Intensity":6,"R":245,"G":0,"B":0},{"Intensity":6.1,"R":238,"G":0,"B":0},{"Intensity":6.2,"R":230,"G":0,"B":0},{"Intensity":6.3,"R":223,"G":0,"B":0},{"Intensity":6.4,"R":215,"G":0,"B":0},{"Intensity":6.5,"R":208,"G":0,"B":0},{"Intensity":6.6,"R":200,"G":0,"B":0},{"Intensity":6.7,"R":192,"G":0,"B":0},{"Intensity":6.8,"R":185,"G":0,"B":0},{"Intensity":6.9,"R":177,"G":0,"B":0},{"Intensity":7.0,"R":170,"G":0,"B":0}]'

_RGB_TO_SI = {(e["R"], e["G"], e["B"]): e["Intensity"] for e in _json.loads(_COLORMAP_JSON)}
_CM_RGB    = np.array(list(_RGB_TO_SI.keys()),    dtype=np.int32)
_CM_SI     = np.array(list(_RGB_TO_SI.values()),  dtype=np.float32)
_PAL_DIST_THRESHOLD = 800

# 状態管理用
_shindo_state = {"time": None, "shindo": {}, "gif_url": None, "error": None}
_shindo_lock  = threading.Lock()
_stations     = []

def build_palette_table(img: Image.Image) -> np.ndarray:
    pal = img.getpalette()
    transparency = img.info.get("transparency")
    table = np.full(256, np.nan, dtype=np.float32)
    for i in range(256):
        if i == transparency: continue
        r, g, b = pal[i*3], pal[i*3+1], pal[i*3+2]
        rgb = np.array([[r, g, b]], dtype=np.int32)
        dist_sq = ((_CM_RGB - rgb) ** 2).sum(axis=1)
        idx = int(dist_sq.argmin())
        if dist_sq[idx] <= _PAL_DIST_THRESHOLD:
            table[i] = _CM_SI[idx]
    return table

def _detect_encoding(path: str) -> str:
    """CSVの文字コードを UTF-8, Shift-JIS の順で試す"""
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift-jis"):
        try:
            with open(path, "rb") as f:
                f.read().decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"  # デフォルト

def load_stations(path: str):
    if not os.path.exists(path):
        print(f"警告: {path} が見つかりません。震度分布は表示されません。")
        return []
    
    stations = []
    # 文字コードを自動判定
    enc = _detect_encoding(path)
    
    try:
        with open(path, newline="", encoding=enc) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    stations.append({
                        "code": row["code"].strip(),
                        "name": row.get("name", "").strip(),
                        "pixel_x": int(row["pixel_x"]),
                        "pixel_y": int(row["pixel_y"]),
                        "lat": float(row["lat"]) if row.get("lat") else None,
                        "lon": float(row["lon"]) if row.get("lon") else None,
                    })
                except (ValueError, KeyError) as e:
                    # 1行のエラーで全体を止めない
                    continue
        print(f"観測点ロード完了: {len(stations)} 件 (Encoding: {enc})")
    except Exception as e:
        print(f"観測点ロードエラー: {e}")
    return stations

def poll_shindo_loop():
    global _stations
    _stations = load_stations(STATIONS_CSV)
    if not _stations: return

    while True:
        tick = time.monotonic()
        try:
            # 最新時刻取得
            r1 = requests.get(LATEST_URL, headers=HEADERS, timeout=5)
            r1.raise_for_status()
            dt_str = r1.json()["latest_time"]
            dt = datetime.datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S")
            
            # GIF取得
            gif_url = f"{GIF_BASE_URL}/{dt.strftime('%Y%m%d')}/{dt.strftime('%Y%m%d%H%M%S')}.jma_s.gif"
            r2 = requests.get(gif_url, headers=HEADERS, timeout=5)
            r2.raise_for_status()
            
            # 解析
            img = Image.open(io.BytesIO(r2.content))
            pal_table = build_palette_table(img)
            imap = pal_table[np.array(img)]
            
            H, W = imap.shape
            res = {}
            for st in _stations:
                px, py = st["pixel_x"], st["pixel_y"]
                if 0 <= py < H and 0 <= px < W:
                    v = imap[py, px]
                    res[st["code"]] = None if np.isnan(v) else float(v)
                else:
                    res[st["code"]] = None
            
            with _shindo_lock:
                _shindo_state.update(time=dt_str, shindo=res, gif_url=gif_url, error=None)
                
        except Exception as e:
            with _shindo_lock:
                _shindo_state["error"] = str(e)
            print(f"[Shindo] Error: {e}")

        time.sleep(max(0.1, POLL_INTERVAL - (time.monotonic() - tick)))

# =============================================================================
#  DB 接続ヘルパー
# =============================================================================

def _read_db_config() -> configparser.ConfigParser:
    cfg  = configparser.ConfigParser()
    path = os.path.join(PROJECT_ROOT, 'config', 'db_config.ini')
    if not os.path.exists(path):
        raise FileNotFoundError(f'config/db_config.ini が見つかりません: {path}')
    cfg.read(path, encoding='utf-8')
    return cfg

def _get_db_path() -> str:
    cfg = _read_db_config()
    rel = cfg['sqlite'].get('database', 'data/hypolist.db')
    return os.path.join(PROJECT_ROOT, rel)

def _get_conn():
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return sqlite3.connect(db_path)

# =============================================================================
#  Discord EEW 通知（強震モニタ）
# =============================================================================
EEW_BASE_URL      = "http://www.kmoni.bosai.go.jp"
EEW_POLL_INTERVAL = 3.0

_discord_cfg  = {"webhook_url": None}
_eew_seen_ids = set()
_eew_id_lock  = threading.Lock()

_EEW_INTENSITY_COLOR = {
    "7":  (180,   0, 104),
    "6強": (165,   0,  33),
    "6弱": (255,  40,   0),
    "5強": (255, 153,   0),
    "5弱": (255, 230,   0),
    "4":  (250, 230, 150),
    "3":  (  0,  65, 255),
    "2":  (  0, 170, 255),
    "1":  (242, 242, 255),
}

def _intensity_color(intensity: str) -> int:
    r, g, b = _EEW_INTENSITY_COLOR.get(intensity, (128, 128, 128))
    return (r << 16) | (g << 8) | b

def _load_discord_cfg():
    try:
        cfg = _read_db_config()
        if cfg.has_section('discord'):
            url = cfg.get('discord', 'webhook_url', fallback=None)
            _discord_cfg["webhook_url"] = url.strip() if url else None
    except Exception as e:
        print(f"[Discord] config読み込みエラー: {e}")

def _send_discord_shindo(content: str):
    """強震モニタ クラスタ検知の Discord 通知（テキスト形式）"""
    url = _discord_cfg.get("webhook_url")
    if not url:
        return
    try:
        requests.post(url, json={"content": content}, timeout=5)
        print(f"[Discord] 強震通知送信: {content.splitlines()[0]}")
    except Exception as e:
        print(f"[Discord] 強震通知送信エラー: {e}")

def _send_discord_quake_event(ev: dict) -> bool:
    """Elapsed Timer の新規地震イベントを Discord に通知する"""
    url = _discord_cfg.get("webhook_url")
    if not url:
        return False
    try:
        mag     = ev.get("mag")
        mag_type = ev.get("magType", "M")
        mags    = ev.get("mags") or []
        lat     = float(ev.get("lat", 0))
        lon     = float(ev.get("lon", 0))
        depth   = float(ev.get("depth", 0))
        ot_str  = ev.get("ot", "")
        region  = ev.get("region") or "不明"

        mag_str  = f"{mag_type}{mag:.1f}" if mag is not None else "M?"
        mags_str = " / ".join(f"{m['type']} {m['val']:.1f}" for m in mags) if len(mags) > 1 else mag_str
        color    = 0xff6b35 if mag and mag >= 7 else 0xffd166 if mag and mag >= 6 else 0x00d4ff
        lat_str  = f"{'N' if lat >= 0 else 'S'}{abs(lat):.3f}°"
        lon_str  = f"{'E' if lon >= 0 else 'W'}{abs(lon):.3f}°"

        payload = {
            "embeds": [{
                "title": "🌍 新規地震イベント検出",
                "color": color,
                "fields": [
                    {"name": "マグニチュード",  "value": mags_str,               "inline": True},
                    {"name": "発生時刻 (JST)", "value": ot_str,                  "inline": True},
                    {"name": "地域",           "value": region,                  "inline": False},
                    {"name": "座標",           "value": f"{lat_str}  {lon_str}", "inline": True},
                    {"name": "深さ",           "value": f"{depth:.0f} km",       "inline": True},
                ],
                "footer":    {"text": "QuakeView — Elapsed Timer"},
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }]
        }
        requests.post(url, json=payload, timeout=5)
        print(f"[Discord] quake event 通知: {region} {mag_str}")
        return True
    except Exception as e:
        print(f"[Discord] quake event 送信エラー: {e}")
        return False

def _send_discord_eew(eew: dict):
    url = _discord_cfg.get("webhook_url")
    if not url:
        return
    try:
        name      = eew.get("region_name") or "不明"
        mag       = eew.get("magunitude", "?")   # 強震モニタ API のtypo: magunitude
        depth_raw = eew.get("depth", "?")        # "10km" 形式
        depth_num = depth_raw.replace("km", "").strip() if isinstance(depth_raw, str) else str(depth_raw)
        intensity = eew.get("calcintensity") or "不明"
        alert_flg = eew.get("alertflg", "")
        is_final  = eew.get("is_final", False)
        report_num= eew.get("report_num", "?")
        latitude  = eew.get("latitude",  "?")
        longitude = eew.get("longitude", "?")
        origin    = eew.get("origin_time",  "")  # "20260520022906"
        report_t  = eew.get("report_time",  "")  # "2026/05/20 02:29:56"

        try:
            rt = datetime.datetime.strptime(report_t, "%Y/%m/%d %H:%M:%S")
            report_hhmmss = rt.strftime("%H:%M:%S")
        except Exception:
            report_hhmmss = report_t

        try:
            ot = datetime.datetime.strptime(origin, "%Y%m%d%H%M%S")
            origin_label = ot.strftime("%Y/%m/%d %H:%M:%S")
        except Exception:
            origin_label = origin

        final_label = "最終報" if is_final else ""

        description = (
            f"{report_hhmmss} {intensity} M{mag} {depth_raw} N{latitude} E{longitude}\n"
            f"【{alert_flg}  第{report_num}報】{final_label}\n"
            f"{name}  で地震\n"
            f"発生時刻: {origin_label}"
        )

        payload = {
            "embeds": [{
                "description": description,
                "color": _intensity_color(intensity),
            }]
        }
        requests.post(url, json=payload, timeout=5)
        print(f"[Discord] 通知送信: {name} M{mag} 震度{intensity}")
    except Exception as e:
        print(f"[Discord] 送信エラー: {e}")

def _check_and_notify_eew(eew: dict):
    if (eew.get("result", {}).get("status") != "success"
            or eew.get("is_cancel")
            or eew.get("is_training")):
        return
    report_id  = eew.get("report_id")
    report_num = eew.get("report_num", "0")
    if not report_id:
        return
    key = f"{report_id}_{report_num}"
    with _eew_id_lock:
        if key in _eew_seen_ids:
            return
        _eew_seen_ids.add(key)
    _send_discord_eew(eew)

def poll_eew_loop():
    _load_discord_cfg()
    if not _discord_cfg.get("webhook_url"):
        print("[Discord] webhook_url が未設定のため EEW 通知を無効化します")
        print("  → config/db_config.ini の [discord] セクションに webhook_url を追加してください")
        return

    print("[Discord] 強震モニタ EEW 通知 有効")
    eew_headers = {**HEADERS, "Referer": f"{EEW_BASE_URL}/"}

    while True:
        tick = time.monotonic()
        try:
            r1 = requests.get(LATEST_URL, headers=HEADERS, timeout=5)
            r1.raise_for_status()
            dt_str = r1.json()["latest_time"]
            dt = datetime.datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S")

            eew_url = f"{EEW_BASE_URL}/webservice/hypo/eew/{dt.strftime('%Y%m%d%H%M%S')}.json"
            r2 = requests.get(eew_url, headers=eew_headers, timeout=5)
            if r2.status_code == 200:
                _check_and_notify_eew(r2.json())
        except Exception as e:
            print(f"[EEW] エラー: {e}")

        time.sleep(max(0.1, EEW_POLL_INTERVAL - (time.monotonic() - tick)))

# =============================================================================
#  JMA 地震情報フィード通知
# =============================================================================
JMA_FEED_URL      = "https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml"
JMA_FEED_INTERVAL = 60.0
_ATOM_NS          = "http://www.w3.org/2005/Atom"
_JMA_NS           = "http://xml.kishou.go.jp/jmaxml1/"

# =============================================================================
#  VTSE41 津波警報・注意報・予報 XMLパーサー
# =============================================================================

def _vtse_grade(kind_code: str) -> str:
    """JMA津波電文 Category/Kind/Code → フロントエンド grade キー"""
    if kind_code in ('51', '52', '53'):
        return 'MajorWarning'   # 大津波警報
    if kind_code == '61':
        return 'Warning'        # 津波警報
    if kind_code == '62':
        return 'Watch'          # 津波注意報
    return 'None'               # 71=予報（若干の海面変動）, 00=なし

def _parse_vtse41(xml_bytes: bytes) -> dict:
    """VTSE41（津波警報・注意報・予報）XMLを解析して正規化JSONを返す"""
    root = ET.fromstring(xml_bytes)

    rdt_el       = _xml_find(root, 'Head', 'ReportDateTime')
    info_type_el = _xml_find(root, 'Head', 'InfoType')
    rdt       = rdt_el.text.strip()       if rdt_el is not None       and rdt_el.text       else None
    info_type = info_type_el.text.strip() if info_type_el is not None and info_type_el.text else ''

    areas = []
    forecast = _xml_find(root, 'Body', 'Tsunami', 'Forecast')
    if forecast is not None:
        for item in _xml_findall(forecast, 'Item'):
            area_el = _xml_find(item, 'Area')
            if area_el is None:
                continue
            name_el = _xml_find(area_el, 'Name')
            code_el = _xml_find(area_el, 'Code')
            name    = name_el.text.strip() if name_el is not None and name_el.text else ''
            code    = code_el.text.strip() if code_el is not None and code_el.text else ''

            kind_code_el = _xml_find(item, 'Category', 'Kind', 'Code')
            kind_code    = kind_code_el.text.strip() if kind_code_el is not None and kind_code_el.text else '00'

            fh_el     = _xml_find(item, 'FirstHeight')
            arrival   = None
            condition = ''
            if fh_el is not None:
                arr_el  = _xml_find(fh_el, 'ArrivalTime')
                cond_el = _xml_find(fh_el, 'Condition')
                arrival   = arr_el.text.strip()  if arr_el  is not None and arr_el.text  else None
                condition = cond_el.text.strip() if cond_el is not None and cond_el.text else ''

            th_el       = _xml_find(item, 'MaxHeight', 'TsunamiHeight')
            height_desc = th_el.get('description', '') if th_el is not None else ''

            areas.append({
                'code': code, 'name': name, 'grade': _vtse_grade(kind_code),
                'maxHeight':   {'description': height_desc},
                'firstHeight': {'arrivalTime': arrival, 'condition': condition},
            })

    return {'rdt': rdt, 'infoType': info_type, 'areas': areas}

_jma_seen_ids = set()
_jma_id_lock  = threading.Lock()

def _fetch_xml(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 QuakeView/1.0"})
    with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as r:
        return r.read()

def _xml_local(tag: str) -> str:
    """'{namespace}LocalName' → 'LocalName'"""
    return tag.split('}')[-1] if '}' in tag else tag

def _xml_find(node, *local_names):
    """名前空間を無視してローカル名のパスで要素を検索（再帰）"""
    if not local_names:
        return node
    for child in node:
        if _xml_local(child.tag) == local_names[0]:
            result = _xml_find(child, *local_names[1:])
            if result is not None:
                return result
    return None

def _xml_findall(node, local_name):
    """直下の子要素を名前空間無視で全取得"""
    return [c for c in node if _xml_local(c.tag) == local_name]

def _parse_vxse53(xml_bytes: bytes) -> dict:
    """VXSE53（震源・震度に関する情報）XMLを解析して主要フィールドを返す"""
    root = ET.fromstring(xml_bytes)

    def txt(*path):
        el = _xml_find(root, *path)
        return el.text.strip() if el is not None and el.text else ""

    origin_time = txt("Body", "Earthquake", "OriginTime")
    hypo_name   = txt("Body", "Earthquake", "Hypocenter", "Area", "Name")
    mag_el      = _xml_find(root, "Body", "Earthquake", "Magnitude")
    mag         = mag_el.text.strip() if mag_el is not None and mag_el.text else "?"
    mag_desc    = (mag_el.get("description") or f"M{mag}") if mag_el is not None else f"M{mag}"
    coord_el    = _xml_find(root, "Body", "Earthquake", "Hypocenter", "Area", "Coordinate")
    coord_desc  = coord_el.get("description", "") if coord_el is not None else ""
    max_int     = txt("Body", "Intensity", "Observation", "MaxInt")

    # OriginTime は JST (+09:00) なのでそのまま表示
    try:
        ot = datetime.datetime.strptime(origin_time[:19], "%Y-%m-%dT%H:%M:%S")
        origin_label = ot.strftime("%Y/%m/%d %H:%M")
    except Exception:
        origin_label = origin_time

    return {
        "origin_label": origin_label,
        "hypo_name":    hypo_name,
        "coord_desc":   coord_desc,
        "mag_desc":     mag_desc,
        "max_int":      max_int,
    }

def _send_discord_quake_info(quake: dict):
    url = _discord_cfg.get("webhook_url")
    if not url:
        return
    try:
        intensity = quake["max_int"]
        lines = [
            f"震源: {quake['hypo_name']}",
            f"規模: {quake['mag_desc']}",
        ]
        if quake["coord_desc"]:
            lines.append(f"震源情報: {quake['coord_desc']}")
        if intensity:
            lines.append(f"最大震度: {intensity}")
        lines.append(f"発生時刻: {quake['origin_label']}")

        payload = {
            "embeds": [{
                "title":       "🌐 震源・震度情報",
                "description": "\n".join(lines),
                "color":       _intensity_color(intensity) if intensity else 0x888888,
            }]
        }
        requests.post(url, json=payload, timeout=5)
        print(f"[JMA] 通知送信: {quake['hypo_name']} {quake['mag_desc']} 震度{intensity}")
    except Exception as e:
        print(f"[JMA] 送信エラー: {e}")

def poll_jma_feed_loop():
    _load_discord_cfg()
    if not _discord_cfg.get("webhook_url"):
        return

    print("[JMA] 地震情報フィード監視 開始（60秒ごと）")

    # 起動時の既存エントリを既読にして再起動時の重複通知を防ぐ
    try:
        data = _fetch_xml(JMA_FEED_URL)
        root = ET.fromstring(data)
        with _jma_id_lock:
            for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
                id_el = entry.find(f"{{{_ATOM_NS}}}id")
                if id_el is not None and id_el.text:
                    _jma_seen_ids.add(id_el.text.strip())
        print(f"[JMA] 既存エントリ {len(_jma_seen_ids)} 件を既読")
    except Exception as e:
        print(f"[JMA] 初期化エラー: {e}")

    while True:
        tick = time.monotonic()
        try:
            data = _fetch_xml(JMA_FEED_URL)
            root = ET.fromstring(data)

            for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
                # 震源・震度に関する情報のみ対象
                title_el = entry.find(f"{{{_ATOM_NS}}}title")
                if title_el is None or title_el.text != "震源・震度に関する情報":
                    continue

                id_el    = entry.find(f"{{{_ATOM_NS}}}id")
                entry_id = id_el.text.strip() if id_el is not None and id_el.text else ""

                with _jma_id_lock:
                    if entry_id in _jma_seen_ids:
                        continue
                    _jma_seen_ids.add(entry_id)

                # 個別 XML を取得・解析して通知
                link_el = entry.find(f"{{{_ATOM_NS}}}link")
                xml_url = link_el.get("href", "") if link_el is not None else ""
                if xml_url:
                    try:
                        xml_bytes = _fetch_xml(xml_url)
                        quake = _parse_vxse53(xml_bytes)
                        _send_discord_quake_info(quake)
                    except Exception as e:
                        print(f"[JMA] XML解析エラー ({xml_url}): {e}")

        except Exception as e:
            print(f"[JMA] フィードエラー: {e}")

        time.sleep(max(0.1, JMA_FEED_INTERVAL - (time.monotonic() - tick)))

# =============================================================================
#  HTTP ハンドラ
# =============================================================================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed   = urlparse(self.path)
        params   = parse_qs(parsed.query)
        url_path = unquote(parsed.path)

        # プロキシ
        if url_path == '/proxy' and 'url' in params:
            self._handle_proxy(params['url'][0])
            return

        # リアルタイム震度 API
        if url_path == '/api/shindo':
            with _shindo_lock:
                shindo_map = dict(_shindo_state["shindo"])
                t = _shindo_state["time"]
                err = _shindo_state["error"]
            
            data = {
                "time": t, "error": err,
                "stations": [
                    {"code": st["code"], "name": st["name"], "lat": st["lat"], "lon": st["lon"], "shindo": shindo_map.get(st["code"])}
                    for st in _stations if st["lat"] is not None
                ]
            }
            self._send_json(200, data)
            return

        # 観測点情報 API
        if url_path == '/api/stations':
            self._send_json(200, _stations)
            return

        # 津波情報 API（JMA XML フィード → VTSE41 解析）
        if url_path == '/api/tsunami':
            self._handle_tsunami()
            return

        # 海保潮位スクレイピング API
        if url_path == '/api/tide/jcg':
            self._handle_jcg_tide(params)
            return

        # DB情報 API
        if url_path == '/api/db/info':
            self._handle_db_info()
            return

        # 静的ファイル配信
        if url_path == '/': url_path = '/index.html'
        file_path = os.path.realpath(os.path.join(PROJECT_ROOT, url_path.lstrip('/')))
        if not file_path.startswith(PROJECT_ROOT + os.sep):
            self._send_error(403, 'Forbidden')
            return

        if os.path.isfile(file_path):
            self._serve_file(file_path)
        else:
            self._send_error(404, f'Not found: {url_path}')

    def do_POST(self):
        url_path = unquote(urlparse(self.path).path)
        length   = int(self.headers.get('Content-Length', 0))
        body     = self.rfile.read(length) if length else b'{}'
        try:
            params = _json.loads(body)
        except:
            self._send_json(400, {'error': 'Invalid JSON'})
            return

        if url_path == '/api/etas/run':
            self._handle_etas_run(params)
        elif url_path == '/api/viz/query':
            self._handle_viz_query(params)
        elif url_path == '/api/notify':
            self._handle_shindo_notify(params)
        elif url_path == '/api/notify/quake':
            self._handle_quake_notify(params)
        elif url_path == '/api/predict':
            self._handle_predict(params)
        else:
            self._send_error(404, 'Not found')

    # 既存のヘルパーメソッド群（略さず保持）
    def _handle_proxy(self, target: str):
        import gzip as _gzip
        try:
            req = urllib.request.Request(target, headers={'User-Agent': 'Mozilla/5.0 QuakeView/1.0'})
            with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as r:
                data     = r.read()
                ct       = r.headers.get('Content-Type', 'application/octet-stream')
                enc      = r.headers.get('Content-Encoding', '')
            # gzip 圧縮のまま配信されるファイル（JMA bosai/hypo GeoJSON など）を展開
            if enc == 'gzip' or data[:2] == b'\x1f\x8b':
                try:
                    data = _gzip.decompress(data)
                except Exception:
                    pass  # 展開失敗なら生データのまま送る
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_error(502, str(e))

    def _handle_db_info(self):
        try:
            conn = _get_conn(); cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cur.fetchall()]
            result = []
            for tbl in tables:
                cur.execute(f'PRAGMA table_info("{tbl}")')
                cols = [{'name': r[1], 'type': r[2]} for r in cur.fetchall()]
                cur.execute(f'SELECT COUNT(*) FROM "{tbl}"')
                count = cur.fetchone()[0]
                result.append({'name': tbl, 'rows': count, 'columns': cols})
            cur.close(); conn.close()
            self._send_json(200, {'tables': result})
        except Exception as e: self._send_json(500, {'error': str(e)})

    def _handle_etas_run(self, params: dict):
        # ... (元の etas_run ロジック) ...
        # (長いので中身は元のコードと同じものを実装)
        csv_path = None; json_path = None
        try:
            col_dt, col_mag = params.get('col_datetime',''), params.get('col_mag','')
            if not col_dt or not col_mag: return self._send_json(400, {'error': 'Missing cols'})
            fd, csv_path = tempfile.mkstemp(suffix='.csv'); fd2, json_path = tempfile.mkstemp(suffix='.json')
            os.close(fd); os.close(fd2)
            
            inline_csv = params.get('inline_csv')
            if inline_csv:
                with open(csv_path, 'w', encoding='utf-8') as f: f.write(inline_csv)
            else:
                table = params.get('table','')
                conn = _get_conn(); cur = conn.cursor()
                sql = f'SELECT "{col_dt}", "{col_mag}" FROM "{table}" ORDER BY "{col_dt}" ASC'
                cur.execute(sql); rows = cur.fetchall()
                with open(csv_path, 'w', encoding='utf-8') as f:
                    f.write(f"{col_dt},{col_mag}\n")
                    for r in rows: f.write(f"{r[0]},{r[1]}\n")
                cur.close(); conn.close()

            etas_script = os.path.join(PROJECT_ROOT, 'py', 'tools.py')
            cmd = [sys.executable, etas_script, 'etas', csv_path, '--output', json_path, '--datetime', col_dt, '--mag', col_mag]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            with open(json_path, 'r', encoding='utf-8') as f: output = _json.load(f)
            self._send_json(200, output)
        except Exception as e: self._send_json(500, {'error': str(e)})
        finally:
            for p in [csv_path, json_path]: 
                if p and os.path.exists(p): os.unlink(p)

    def _handle_viz_query(self, params: dict):
        try:
            table       = params.get('table', '')
            col_dt      = params.get('col_datetime', '')
            col_mag     = params.get('col_mag', '')
            col_lat     = params.get('col_lat', '')
            col_lon     = params.get('col_lon', '')
            col_depth   = params.get('col_depth', '')
            col_place   = params.get('col_place') or None
            date_from   = params.get('date_from')
            date_to     = params.get('date_to')
            lat_min     = params.get('lat_min')
            lat_max     = params.get('lat_max')
            lon_min     = params.get('lon_min')
            lon_max     = params.get('lon_max')
            limit       = int(params.get('limit') or 0)

            if not table or not col_dt or not col_mag or not col_lat or not col_lon or not col_depth:
                return self._send_json(400, {'error': 'Missing required parameters'})

            conn = _get_conn()
            cur = conn.cursor()

            # SELECT 句：必須列 + 任意の震源地列
            place_col = f', "{col_place}"' if col_place else ""
            sql = (
                f'SELECT "{col_dt}", "{col_mag}", "{col_lat}", "{col_lon}", "{col_depth}"{place_col}'
                f' FROM "{table}" WHERE 1=1'
            )
            args = []

            if date_from:
                sql += f' AND "{col_dt}" >= ?'
                args.append(date_from)
            if date_to:
                sql += f' AND "{col_dt}" <= ?'
                args.append(date_to + ' 23:59:59')
            if lat_min is not None:
                sql += f' AND "{col_lat}" >= ?'; args.append(float(lat_min))
            if lat_max is not None:
                sql += f' AND "{col_lat}" <= ?'; args.append(float(lat_max))
            if lon_min is not None:
                sql += f' AND "{col_lon}" >= ?'; args.append(float(lon_min))
            if lon_max is not None:
                sql += f' AND "{col_lon}" <= ?'; args.append(float(lon_max))

            sql += f' ORDER BY "{col_dt}" ASC'
            if limit > 0:
                sql += f" LIMIT {int(limit)}"

            cur.execute(sql, args)
            db_rows = cur.fetchall()
            cur.close()
            conn.close()

            # ── フロントエンドが期待するキー名に変換 ──
            result = []
            for r in db_rows:
                dt_val = r[0]
                # datetime オブジェクト / 文字列 → ISO 8601形式
                # （JSの new Date() が確実にパースできるよう T区切りに統一）
                if isinstance(dt_val, datetime.datetime):
                    dt_str = dt_val.strftime('%Y-%m-%dT%H:%M:%S')
                elif isinstance(dt_val, datetime.date):
                    dt_str = dt_val.strftime('%Y-%m-%dT%H:%M:%S')
                elif dt_val:
                    # SQLite TEXT: "2026-03-07 00:00:16.01" → "2026-03-07T00:00:16"
                    dt_str = str(dt_val).replace(' ', 'T')[:19]
                else:
                    dt_str = None

                row_dict = {
                    'dt'   : dt_str,
                    'mag'  : float(r[1]) if r[1] is not None else None,
                    'lat'  : float(r[2]) if r[2] is not None else None,
                    'lon'  : float(r[3]) if r[3] is not None else None,
                    'depth': float(r[4]) if r[4] is not None else None,
                    'place': str(r[5])   if col_place and len(r) > 5 else '',
                }
                result.append(row_dict)

            self._send_json(200, {'rows': result, 'n_total': len(result)})

        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _serve_file(self, file_path: str):
        with open(file_path, 'rb') as f: data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', get_mime(file_path))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code: int, obj):
        body = _json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _handle_shindo_notify(self, params: dict):
        """POST /api/notify — ブラウザ側クラスタ検知 → Discord 転送"""
        content = params.get('content', '')
        if not isinstance(content, str) or not content.strip():
            self._send_json(400, {'error': 'content が空です'})
            return
        _send_discord_shindo(content.strip())
        self._send_json(200, {'ok': True})

    def _handle_quake_notify(self, params: dict):
        """POST /api/notify/quake — Elapsed Timer 新規地震イベント → Discord 転送"""
        ok = _send_discord_quake_event(params)
        if ok:
            self._send_json(200, {'ok': True})
        else:
            self._send_json(503, {'error': 'webhook未設定または送信失敗'})

    def _handle_predict(self, params: dict):
        """POST /api/predict — 震度分布予測AIを実行してJSONを返す"""
        try:
            lat   = float(params['lat'])
            lon   = float(params['lon'])
            depth = float(params['depth'])
            mag   = float(params['mag'])
        except (KeyError, ValueError, TypeError) as e:
            self._send_json(400, {'error': f'パラメータ不正: {e}'})
            return

        predict_script = os.path.join(PROJECT_ROOT, 'py', 'tools.py')
        if not os.path.exists(predict_script):
            self._send_json(500, {'error': f'tools.py が見つかりません: {predict_script}'})
            return

        try:
            proc = subprocess.run(
                [sys.executable, predict_script, 'predict',
                 '--lat',   str(lat),
                 '--lon',   str(lon),
                 '--depth', str(depth),
                 '--mag',   str(mag),
                 '--json'],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                self._send_json(500, {'error': proc.stderr[-500:] or '予測スクリプトが失敗しました'})
                return
            data = _json.loads(proc.stdout)
            self._send_json(200, data)
        except subprocess.TimeoutExpired:
            self._send_json(500, {'error': 'タイムアウト（120秒）'})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _handle_tsunami(self):
        """GET /api/tsunami — JMA XML フィードから最新の津波警報情報を返す"""
        try:
            feed_data = _fetch_xml(JMA_FEED_URL)
            feed_root = ET.fromstring(feed_data)

            latest_url = None
            for entry in feed_root.findall(f"{{{_ATOM_NS}}}entry"):
                title_el = entry.find(f"{{{_ATOM_NS}}}title")
                if title_el is None or title_el.text != '津波警報・注意報・予報':
                    continue
                link_el = entry.find(f"{{{_ATOM_NS}}}link")
                if link_el is not None:
                    href = link_el.get('href', '')
                    if href:
                        latest_url = href
                        break

            if not latest_url:
                self._send_json(200, {'rdt': None, 'infoType': '解除', 'areas': []})
                return

            xml_bytes = _fetch_xml(latest_url)
            result    = _parse_vtse41(xml_bytes)
            self._send_json(200, result)
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _handle_jcg_tide(self, params: dict):
        """
        GET /api/tide/jcg?station=0016[&date=20260601]
        海上保安庁 潮位観測ページをスクレイピングして JSON を返す。
        出典：海上保安庁ホームページ (https://www1.kaiho.mlit.go.jp/TIDE/gauge/)
        データを加工（整形・Canvas グラフ描画）して表示。
        """
        station = params.get('station', [''])[0].strip()
        date    = params.get('date',    [''])[0].strip()

        if not re.match(r'^\d{4}$', station):
            self._send_json(400, {'error': 'station は4桁の数字で指定してください'})
            return

        gauge_url = f'https://www1.kaiho.mlit.go.jp/TIDE/gauge/gauge.php?s={station}'

        try:
            if date and re.match(r'^\d{8}$', date):
                # POST で特定日付を取得
                from urllib.parse import urlencode
                post_data = urlencode({'dspymd': date}).encode('ascii')
                req = urllib.request.Request(
                    gauge_url,
                    data=post_data,
                    headers={
                        'User-Agent':   'Mozilla/5.0 QuakeView/1.0',
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Referer':      gauge_url,
                    }
                )
            else:
                req = urllib.request.Request(
                    gauge_url,
                    headers={'User-Agent': 'Mozilla/5.0 QuakeView/1.0'}
                )

            with urllib.request.urlopen(req, timeout=12, context=SSL_CTX) as r:
                raw = r.read()

            html = raw.decode('utf-8', errors='replace')

            # ── <pre> ブロック抽出 ──────────────────────────────
            pre_m = re.search(r'<pre>(.*?)</pre>', html, re.S | re.I)
            if not pre_m:
                self._send_json(404, {'error': 'データブロックが見つかりません（対応外の観測点の可能性）'})
                return

            pre = pre_m.group(1)

            # メタデータ抽出
            meta = {}
            for pattern, key in [
                (r'Location\s+(\S+)',        'location'),
                (r'Longitude\s+([0-9\-]+)',  'longitude'),
                (r'Latitude\s+([0-9\-]+)',   'latitude'),
                (r'TidalHeightDatum\s+(.+)', 'datum'),
            ]:
                m = re.search(pattern, pre)
                if m:
                    meta[key] = m.group(1).strip()

            # 5分値データ解析
            # フォーマット: YYYY MM DD HH MM   cm
            data_5min = []
            for line in pre.split('\n'):
                line = line.strip()
                m = re.match(
                    r'(\d{4})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(-?\d+)',
                    line
                )
                if not m:
                    continue
                y, mo, d, h, mi, v_str = m.groups()
                v = int(v_str)
                data_5min.append({
                    'time':  f'{h}:{mi}',
                    'value': None if v == 9999 else v,
                })

            if not data_5min:
                self._send_json(404, {'error': 'データ行が解析できませんでした'})
                return

            # 毎時テーブル解析
            hourly = []
            table_m = re.search(r'<table[^>]*>.*?</table>', html, re.S | re.I)
            if table_m:
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_m.group(), re.S | re.I)
                if len(rows) >= 2:
                    cells = re.findall(r'<td[^>]*>(.*?)</td>', rows[1], re.S | re.I)
                    cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                    for i, c in enumerate(cells[1:], 0):   # cells[0] は日付
                        try:
                            hourly.append({'hour': i, 'value': int(c)})
                        except ValueError:
                            hourly.append({'hour': i, 'value': None})

            # 観測済み点数・範囲
            obs_vals = [p['value'] for p in data_5min if p['value'] is not None]

            result = {
                'station':     station,
                'interval':    5,
                'meta':        meta,
                'data':        data_5min,
                'hourly':      hourly,
                'obs_count':   len(obs_vals),
                'total_count': len(data_5min),
                'source':      '海上保安庁',
                'source_url':  gauge_url,
                'attribution': (
                    f'出典：海上保安庁ホームページ ({gauge_url}) '
                    '／ QuakeViewにて数値データを加工・グラフ描画'
                ),
            }
            self._send_json(200, result)

        except urllib.error.URLError as e:
            self._send_json(502, {'error': f'海保サーバーに接続できませんでした: {e}'})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _send_error(self, code: int, msg: str):
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, format, *args): pass

# =============================================================================
#  スケジューラ (JMA自動取得)
# =============================================================================
def scraper_scheduler():
    tools_path = os.path.join(PROJECT_ROOT, 'py', 'tools.py')
    if not os.path.exists(tools_path): return
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now >= target: target += datetime.timedelta(days=1)
        time.sleep((target - now).total_seconds())
        try:
            subprocess.run([sys.executable, tools_path, 'scrape-hinet', '--auto'], cwd=PROJECT_ROOT)
        except: pass

# =============================================================================
#  メイン
# =============================================================================
if __name__ == '__main__':
    print(f"Starting Integrated Server on http://localhost:{PORT}")

    # 0. Discord config を起動時にロード（/api/notify/quake に備えて）
    _load_discord_cfg()

    # 1. リアルタイム震度解析スレッド
    t1 = threading.Thread(target=poll_shindo_loop, daemon=True)
    t1.start()

    # 2. スクレイパースケジューラスレッド
    t2 = threading.Thread(target=scraper_scheduler, daemon=True)
    t2.start()

    # 3. Discord EEW 通知スレッド
    t3 = threading.Thread(target=poll_eew_loop, daemon=True)
    t3.start()

    # 4. Discord JMA 地震情報通知スレッド
    t4 = threading.Thread(target=poll_jma_feed_loop, daemon=True)
    t4.start()

    # 5. Webブラウザ起動
    threading.Timer(1.0, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()

    # 6. サーバー起動
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")