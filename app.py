import os
import json
import requests
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
import streamlit.components.v1 as components

# -----------------------------------------------------------------------------
# 0. 만원 단위 숫자를 'X억 Y만' 한글 단위로 변환하는 함수
# -----------------------------------------------------------------------------
def format_korean_price(price_manwon):
    if pd.isna(price_manwon) or price_manwon <= 0:
        return "0만"
    
    price = int(price_manwon)
    uk = price // 10000
    man = price % 10000
    
    if uk > 0 and man > 0:
        return f"{uk:,}억 {man:,}만"
    elif uk > 0:
        return f"{uk:,}억"
    else:
        return f"{man:,}만"

# XML 안전 태그 추출 함수
def get_xml_text(item, tags, default=""):
    for tag in tags:
        val = item.findtext(tag)
        if val is not None and val.strip():
            return val.strip()
    return default

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정 및 CSS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="부동산 실거래가 지도", layout="wide")

st.markdown("""
<style>
    /* 사이드바용 컴팩트 안내 상자 */
    .info-banner-sidebar {
        background-color: #064e3b;
        border: 1px solid #10b981;
        border-radius: 8px;
        padding: 12px 14px;
        margin-top: 15px;
        font-size: 13px;
        line-height: 1.6;
        color: #ecfdf5;
    }

    [data-testid="stSidebar"] {
        min-width: 350px !important;
        max-width: 500px !important;
    }

    /* 1. Streamlit 상단 투명 헤더 바 완전 숨김 */
    header[data-testid="stHeader"] {
        display: none !important;
    }

    /* 2. 메인 영역 상단 여백 충분히 확보 (잘림 방지) */
    .block-container {
        padding-top: 3.5rem !important;
        padding-bottom: 0rem !important;
    }

    /* 3. 로딩 스피너 화면 전체 오버레이 & 중앙 컴팩트 모달 박스 */
    div[data-testid="stSpinner"] {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        background: rgba(0, 0, 0, 0.65) !important;
        backdrop-filter: blur(2px) !important;
        z-index: 999999 !important;
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
    }
    div[data-testid="stSpinner"] > div,
    div[data-testid="stSpinner"] [role="alert"] {
        width: auto !important;
        max-width: fit-content !important;
        background-color: #1f2937 !important;
        padding: 16px 24px !important;
        border-radius: 12px !important;
        border: 1px solid #374151 !important;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5) !important;
        color: #ffffff !important;
    }
</style>
""", unsafe_allow_html=True)

try:
    KAKAO_REST_KEY = st.secrets["KAKAO_REST_KEY"]
    MOLIT_SERVICE_KEY = st.secrets["MOLIT_SERVICE_KEY"]
    KAKAO_JS_KEY = st.secrets["KAKAO_JS_KEY"]
except Exception:
    st.error("⚠️ Streamlit Secrets에 API 키가 설정되지 않았습니다.")
    KAKAO_REST_KEY = ""
    MOLIT_SERVICE_KEY = ""
    KAKAO_JS_KEY = ""

# -----------------------------------------------------------------------------
# 2. 카카오 지도 및 네이버 스타일 슬라이드 패널 HTML 생성
# -----------------------------------------------------------------------------
MAP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "kakao_map_comp"))
os.makedirs(MAP_DIR, exist_ok=True)
INDEX_HTML_PATH = os.path.join(MAP_DIR, "index.html")

INDEX_HTML_CONTENT = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script type="text/javascript" src="https://dapi.kakao.com/v2/maps/sdk.js?appkey=""" + KAKAO_JS_KEY + """&libraries=clusterer"></script>
    <style>
        html, body { width: 100%; height: 100%; margin: 0; padding: 0; background: transparent; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; }
        
        #map-container {
            position: relative;
            width: 100%;
            height: 800px;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #374151;
        }

        #map { width: 100%; height: 100%; }

        #detail-panel {
            position: absolute;
            top: 0;
            right: -430px;
            width: 410px;
            height: 100%;
            background: #111827;
            color: #f3f4f6;
            box-shadow: -5px 0 25px rgba(0, 0, 0, 0.5);
            transition: right 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 1000;
            display: flex;
            flex-direction: column;
            box-sizing: border-box;
            border-left: 1px solid #374151;
        }

        #detail-panel.open {
            right: 0;
        }

        .panel-header {
            padding: 16px;
            background: #1f2937;
            border-bottom: 1px solid #374151;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }

        .panel-title {
            font-size: 17px;
            font-weight: bold;
            color: #60a5fa;
            margin: 0 0 4px 0;
        }

        .panel-sub {
            font-size: 13px;
            color: #9ca3af;
        }

        .close-btn {
            background: #374151;
            border: none;
            color: #9ca3af;
            font-size: 14px;
            border-radius: 50%;
            width: 28px;
            height: 28px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s, color 0.2s;
        }
        .close-btn:hover { background: #ef4444; color: #ffffff; }

        .panel-body {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
        }

        .panel-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: center;
        }

        .panel-table th, .panel-table td {
            padding: 10px 4px;
            border-bottom: 1px solid #374151;
        }

        .panel-table th {
            background: #1f2937;
            color: #d1d5db;
            position: sticky;
            top: 0;
            z-index: 10;
            font-weight: 600;
        }

        .panel-table tr:nth-child(even) { background-color: #111827; }
        .panel-table tr:nth-child(odd) { background-color: #1f2937; }
        .panel-table tr:hover { background-color: #374151; }

        .custom-overlay-card {
            cursor: pointer;
            padding: 6px 10px;
            background: white;
            color: #2c3e50;
            border: 2px solid #e74c3c;
            border-radius: 10px;
            font-weight: bold;
            font-size: 12px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.25);
            text-align: center;
            user-select: none;
            transition: transform 0.15s ease, border-color 0.15s ease;
        }
        .custom-overlay-card:hover {
            transform: scale(1.06);
            border-color: #2563eb;
        }
    </style>
</head>
<body>
    <div id="map-container">
        <div id="map"></div>

        <div id="detail-panel">
            <div class="panel-header">
                <div>
                    <div id="panel-title" class="panel-title">물건 정보</div>
                    <div id="panel-sub" class="panel-sub">거래 내역 0건</div>
                </div>
                <button class="close-btn" onclick="closePanel()">✕</button>
            </div>
            <div class="panel-body">
                <table class="panel-table">
                    <thead>
                        <tr>
                            <th style="width:12%;">NO</th>
                            <th style="width:22%;">면적</th>
                            <th style="width:18%;">층</th>
                            <th style="width:26%;">매매가</th>
                            <th style="width:22%;">계약일</th>
                        </tr>
                    </thead>
                    <tbody id="panel-table-body">
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        var map, clusterer, markers = [], overlays = [];
        var allTradeData = [];

        function sendToStreamlit(type, data) {
            var msg = Object.assign({ isStreamlitMessage: true, type: type }, data);
            window.parent.postMessage(msg, "*");
        }

        function closePanel() {
            document.getElementById('detail-panel').classList.remove('open');
        }

        function openPanel(aptName) {
            var panel = document.getElementById('detail-panel');
            var title = document.getElementById('panel-title');
            var sub = document.getElementById('panel-sub');
            var tbody = document.getElementById('panel-table-body');

            var trades = allTradeData.filter(function(d) { return d.apt_name === aptName; });

            if (trades.length === 0) {
                closePanel();
                return;
            }

            title.innerText = aptName;
            
            var sum = trades.reduce(function(acc, cur) { return acc + cur.price_raw; }, 0);
            var avg = Math.round(sum / trades.length);
            var avgStr = formatKoreanPriceJS(avg);

            sub.innerHTML = '총 <b style="color:#60a5fa;">' + trades.length + '</b>건 | 평균 <b style="color:#f87171;">' + avgStr + '</b>';

            tbody.innerHTML = '';
            trades.forEach(function(t, idx) {
                var tr = document.createElement('tr');
                tr.innerHTML = '<td>' + (idx + 1) + '</td>' +
                               '<td>' + t.area + '㎡</td>' +
                               '<td>' + t.floor + '</td>' +
                               '<td style="color:#f87171; font-weight:bold;">' + t.price_fmt + '</td>' +
                               '<td>' + t.deal_date + '</td>';
                tbody.appendChild(tr);
            });

            panel.classList.add('open');
        }

        function formatKoreanPriceJS(price) {
            if (!price || price <= 0) return '0만';
            var uk = Math.floor(price / 10000);
            var man = price % 10000;
            if (uk > 0 && man > 0) return uk.toLocaleString() + '억 ' + man.toLocaleString() + '만';
            if (uk > 0) return uk.toLocaleString() + '억';
            return man.toLocaleString() + '만';
        }

        window.addEventListener("message", function(event) {
            if (event.data && event.data.type === "streamlit:render") {
                renderMap(event.data.args);
            }
        });

        sendToStreamlit("streamlit:componentReady", { apiVersion: 1 });
        sendToStreamlit("streamlit:setFrameHeight", { height: 815 });

        function renderMap(props) {
            var centerLat = props.center_lat;
            var centerLng = props.center_lng;
            var aptSummary = props.apt_summary;
            allTradeData = props.all_trades || [];

            kakao.maps.load(function() {
                var container = document.getElementById('map');
                if (!map) {
                    map = new kakao.maps.Map(container, {
                        center: new kakao.maps.LatLng(centerLat, centerLng),
                        level: 3
                    });
                } else {
                    map.setCenter(new kakao.maps.LatLng(centerLat, centerLng));
                    map.setLevel(3);
                }

                if (clusterer) clusterer.clear();
                markers.forEach(function(m) { m.setMap(null); });
                markers = [];
                overlays.forEach(function(o) { o.setMap(null); });
                overlays = [];

                clusterer = new kakao.maps.MarkerClusterer({
                    map: map,
                    averageCenter: true,
                    minLevel: 5
                });

                if (aptSummary && aptSummary.length > 0) {
                    aptSummary.forEach(function(item) {
                        var pos = new kakao.maps.LatLng(item.lat, item.lng);
                        var marker = new kakao.maps.Marker({ position: pos, clickable: true });

                        // 클릭 시 마커 위치로 지도 이동 (panTo)
                        kakao.maps.event.addListener(marker, 'click', function() {
                            map.panTo(pos);
                            openPanel(item.apt_name);
                        });

                        var div = document.createElement('div');
                        div.className = 'custom-overlay-card';
                        div.innerHTML = item.apt_name + '<br><span style="color:#e74c3c; font-size:13px;">평균 ' + item.avg_price_fmt + '</span> <span style="font-size:11px; color:#7f8c8d;">(' + item.count + '건)</span>';

                        // 클릭 시 커스텀 카드 위치로 지도 이동 (panTo)
                        div.addEventListener('click', function(e) {
                            e.stopPropagation();
                            map.panTo(pos);
                            openPanel(item.apt_name);
                        });

                        var overlay = new kakao.maps.CustomOverlay({
                            position: pos, 
                            clickable: true, 
                            content: div, 
                            yAnchor: 2.2,
                            zIndex: 1
                        });

                        div.addEventListener('mouseenter', function() { overlay.setZIndex(999); });
                        div.addEventListener('mouseleave', function() { overlay.setZIndex(1); });

                        overlay.setMap(map);

                        markers.push(marker);
                        overlays.push(overlay);
                    });

                    clusterer.addMarkers(markers);
                }
                closePanel();
            });
        }
    </script>
</body>
</html>
"""

with open(INDEX_HTML_PATH, "w", encoding="utf-8") as f:
    f.write(INDEX_HTML_CONTENT)

kakao_map_component = components.declare_component("kakao_map_comp", path=MAP_DIR)

# -----------------------------------------------------------------------------
# 3. 데이터 처리 및 API 함수
# -----------------------------------------------------------------------------
API_ENDPOINTS = {
    "아파트": "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "연립/다세대": "http://apis.data.go.kr/1613000/RTMSDataSvcRHTradeDev/getRTMSDataSvcRHTradeDev",
    "단독/다가구": "http://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",
    "오피스텔": "http://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
    "토지": "http://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"
}

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return R * c

def get_recent_months(n=12):
    today = datetime.now()
    months = []
    for i in range(n):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        months.append(f"{year:04d}{month:02d}")
    return months

def get_nearby_lawd_codes(lat, lng, radius_km=2.5):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    lawd_info = {}
    
    offset = (radius_km / 111.0)
    check_points = [
        (lat, lng),
        (lat + offset, lng),
        (lat - offset, lng),
        (lat, lng + offset),
        (lat, lng - offset)
    ]
    
    for c_lat, c_lng in check_points:
        try:
            region_url = f"https://dapi.kakao.com/v2/local/geo/coord2regioncode.json?x={c_lng}&y={c_lat}"
            reg_res = requests.get(region_url, headers=headers, timeout=3)
            if reg_res.status_code == 200 and reg_res.json().get('documents'):
                reg_doc = reg_res.json()['documents'][0]
                code = reg_doc['code'][:5]
                name = f"{reg_doc.get('region_1depth_name', '')} {reg_doc.get('region_2depth_name', '')}".strip()
                lawd_info[code] = name
        except Exception:
            pass
            
    return lawd_info

def search_location_candidates(query):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    candidates = []
    seen_addrs = set()

    try:
        kw_url = f"https://dapi.kakao.com/v2/local/search/keyword.json?query={query}&size=15"
        res = requests.get(kw_url, headers=headers, timeout=5)
        if res.status_code == 200:
            for doc in res.json().get('documents', []):
                name = doc.get('place_name', '').strip()
                road_addr = doc.get('road_address_name', '').strip()
                jibun_addr = doc.get('address_name', '').strip()
                addr = road_addr if road_addr else jibun_addr
                lat, lng = float(doc['y']), float(doc['x'])
                
                dedup_key = addr if addr else name
                if dedup_key not in seen_addrs:
                    seen_addrs.add(dedup_key)
                    display_name = f"{name} ({addr})" if (addr and name != addr) else (addr if addr else name)
                    candidates.append({
                        'display_name': display_name,
                        'place_name': name,
                        'address': addr,
                        'lat': lat,
                        'lng': lng
                    })
    except Exception:
        pass

    try:
        addr_url = f"https://dapi.kakao.com/v2/local/search/address.json?query={query}&size=10"
        res = requests.get(addr_url, headers=headers, timeout=5)
        if res.status_code == 200:
            for doc in res.json().get('documents', []):
                addr = doc.get('address_name', '').strip()
                lat, lng = float(doc['y']), float(doc['x'])
                
                if addr and addr not in seen_addrs:
                    seen_addrs.add(addr)
                    candidates.append({
                        'display_name': f"[주소] {addr}",
                        'place_name': addr,
                        'address': addr,
                        'lat': lat,
                        'lng': lng
                    })
    except Exception:
        pass

    return candidates

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_molit_single_task(lawd_cd, ymd, property_type):
    api_url = API_ENDPOINTS.get(property_type, API_ENDPOINTS["아파트"])
    items_list = []
    page_no = 1
    
    while True:
        params = {
            'serviceKey': requests.utils.unquote(MOLIT_SERVICE_KEY),
            'LAWD_CD': lawd_cd,
            'DEAL_YMD': ymd,
            'pageNo': str(page_no),
            'numOfRows': '1000'
        }
        try:
            res = requests.get(api_url, params=params, timeout=3)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                items = root.findall('.//item')
                if not items:
                    break
                
                for item in items:
                    umd_name = get_xml_text(item, ['umdNm', 'umdName', 'dong'])
                    jibun_val = get_xml_text(item, ['jibun', 'lnbr'])

                    if property_type == "아파트":
                        apt_name = get_xml_text(item, ['aptNm', 'aptName'], default='아파트')
                    elif property_type == "연립/다세대":
                        apt_name = get_xml_text(item, ['mhbNm', 'mhbName', 'rhNm'], default='연립다세대')
                    elif property_type == "오피스텔":
                        apt_name = get_xml_text(item, ['offiNm', 'offiName', 'aptNm'], default='오피스텔')
                    elif property_type == "단독/다가구":
                        apt_name = get_xml_text(item, ['houseType'], default='단독/다가구')
                    elif property_type == "토지":
                        jimok = get_xml_text(item, ['jimok'], default='-')
                        apt_name = f"토지({jimok})"
                    else:
                        apt_name = "부동산"

                    price_str = get_xml_text(item, ['dealAmount', 'dealAmountManwon'], default='0').replace(',', '').strip()
                    
                    if property_type == "토지":
                        area_val = get_xml_text(item, ['plottageArea', 'pblntfPrc'])
                    elif property_type == "단독/다가구":
                        area_val = get_xml_text(item, ['totalFloorArea', 'totArea'])
                    else:
                        area_val = get_xml_text(item, ['excluUseAr', 'excluArea', 'area'])
                    
                    area = float(area_val) if area_val and area_val != '0' else 0.0
                    floor_val = get_xml_text(item, ['floor'])
                    
                    deal_year = get_xml_text(item, ['dealYear', 'year'])
                    deal_month = get_xml_text(item, ['dealMonth', 'month']).zfill(2)
                    deal_day = get_xml_text(item, ['dealDay', 'day']).zfill(2)
                    
                    items_list.append({
                        "apt_name": apt_name,
                        "umd_name": umd_name,
                        "jibun": jibun_val,
                        "price": int(price_str) if price_str.isdigit() else 0,
                        "area": area,
                        "floor": f"{floor_val}층" if floor_val else "-",
                        "deal_date": f"{deal_year}-{deal_month}-{deal_day}"
                    })
                
                if len(items) < 1000:
                    break
                page_no += 1
            else:
                break
        except Exception:
            break

    return items_list

@st.cache_data(ttl=86400, show_spinner=False)
def get_cached_apt_coord(region_name, umd_name, jibun, apt_name):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    search_queries = []
    
    if umd_name and jibun and apt_name and apt_name not in ["오피스텔", "연립다세대", "단독/다가구", "부동산"] and not apt_name.startswith("토지("):
        search_queries.append((f"{region_name} {umd_name} {jibun} {apt_name}", "keyword"))
    
    if region_name and apt_name and apt_name not in ["오피스텔", "연립다세대", "단독/다가구", "부동산"] and not apt_name.startswith("토지("):
        search_queries.append((f"{region_name} {apt_name}", "keyword"))
        
    if umd_name and jibun:
        search_queries.append((f"{region_name} {umd_name} {jibun}", "address"))
        
    if apt_name and apt_name not in ["오피스텔", "연립다세대", "단독/다가구", "부동산"] and not apt_name.startswith("토지("):
        search_queries.append((apt_name, "keyword"))
        
    if umd_name:
        search_queries.append((f"{region_name} {umd_name}", "address"))

    for query_str, qtype in search_queries:
        try:
            if qtype == "keyword":
                url = f"https://dapi.kakao.com/v2/local/search/keyword.json?query={query_str}"
            else:
                url = f"https://dapi.kakao.com/v2/local/search/address.json?query={query_str}"
            
            res = requests.get(url, headers=headers, timeout=2).json()
            if res.get('documents'):
                doc = res['documents'][0]
                return float(doc['y']), float(doc['x'])
        except Exception:
            pass

    return None, None

# -----------------------------------------------------------------------------
# 4. 고속 병렬 수집 엔진
# -----------------------------------------------------------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_real_estate_ultra_fast(lat, lng, full_address, place_name, property_type, months_count):
    display_addr = full_address if full_address else place_name
    lawd_info = get_nearby_lawd_codes(lat, lng, radius_km=2.5)
    
    if not lawd_info:
        return lat, lng, display_addr, "", [], pd.DataFrame()

    region_list = sorted(list(set(lawd_info.values())))
    months_list = get_recent_months(months_count)
    
    tasks = [(lawd_cd, ymd, property_type) for lawd_cd in lawd_info.keys() for ymd in months_list]

    raw_items = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(fetch_molit_single_task, code, ymd, ptype) for code, ymd, ptype in tasks]
        for future in as_completed(futures):
            res = future.result()
            if res:
                raw_items.extend(res)

    if not raw_items:
        return lat, lng, display_addr, "", region_list, pd.DataFrame()

    unique_locations = {}
    for item in raw_items:
        key = (item['umd_name'], item['jibun'], item['apt_name'])
        if key not in unique_locations:
            unique_locations[key] = item

    coord_cache = {}
    main_region_name = region_list[0] if region_list else ""

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(get_cached_apt_coord, main_region_name, key[0], key[1], key[2]): key 
            for key in unique_locations.keys()
        }
        for future in as_completed(futures):
            key = futures[future]
            c_lat, c_lng = future.result()
            if c_lat is not None and c_lng is not None:
                c_dist = haversine_distance(lat, lng, c_lat, c_lng)
                coord_cache[key] = (c_lat, c_lng, c_dist)

    valid_trades = []
    for trade in raw_items:
        key = (trade['umd_name'], trade['jibun'], trade['apt_name'])
        if key in coord_cache:
            c_lat, c_lng, c_dist = coord_cache[key]
            trade_item = trade.copy()
            trade_item['lat'] = c_lat
            trade_item['lng'] = c_lng
            trade_item['거리(km)'] = c_dist
            valid_trades.append(trade_item)

    if not valid_trades:
        return lat, lng, display_addr, "", region_list, pd.DataFrame()

    df = pd.DataFrame(valid_trades)
    df = df.rename(columns={
        "apt_name": "물건명",
        "price": "매매가(만원)",
        "area": "면적(㎡)",
        "floor": "층수",
        "deal_date": "계약일"
    })
    df = df.sort_values(by=['계약일'], ascending=False).reset_index(drop=True)
    return lat, lng, display_addr, "", region_list, df

# -----------------------------------------------------------------------------
# 5. 사이드바 UI 및 로직
# -----------------------------------------------------------------------------
st.sidebar.markdown(
    "<h3 style='font-size: 20px; font-weight: bold; margin-bottom: 0px;'>🏢 한국자산관리아카데미</h3>", 
    unsafe_allow_html=True
)
st.sidebar.title("📍 주소 및 조건")

with st.sidebar.form(key="search_form"):
    st.markdown("<p style='font-weight: bold; font-size: 16px; margin-bottom: 6px;'>주소 및 건물명</p>", unsafe_allow_html=True)
    search_query_input = st.text_input(
        "검색할 주소 또는 건물명/도로명", 
        value="호수로 688", 
        label_visibility="collapsed"
    )
    
    st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)

    st.markdown("<p style='font-weight: bold; font-size: 16px; margin-bottom: 6px;'>부동산 유형</p>", unsafe_allow_html=True)
    property_type_input = st.selectbox(
        "부동산 유형 선택",
        options=["아파트", "연립/다세대", "단독/다가구", "오피스텔", "토지"],
        index=0,
        label_visibility="collapsed"
    )

    st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)

    st.markdown("<p style='font-weight: bold; font-size: 16px; margin-bottom: 6px;'>조회 기간</p>", unsafe_allow_html=True)
    months_count_input = st.selectbox(
        "조회 기간 선택",
        options=[12, 24, 36],
        index=0,
        format_func=lambda x: f"최근 {x//12}년",
        label_visibility="collapsed"
    )

    st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)

    search_button = st.form_submit_button("🔍 위치 검색", use_container_width=True)

if "candidates" not in st.session_state:
    with st.spinner("🔍 위치를 검색하고 있습니다..."):
        initial_cands = search_location_candidates("호수로 688")
    st.session_state["candidates"] = initial_cands
    st.session_state["selected_candidate_idx"] = 0
    st.session_state["submitted_property_type"] = "아파트"
    st.session_state["submitted_months"] = 12

if search_button:
    with st.spinner("🔍 위치를 검색하고 있습니다..."):
        new_cands = search_location_candidates(search_query_input)
    st.session_state["candidates"] = new_cands
    st.session_state["selected_candidate_idx"] = 0
    st.session_state["submitted_property_type"] = property_type_input
    st.session_state["submitted_months"] = months_count_input

candidates = st.session_state.get("candidates", [])
selected_candidate = None

if candidates:
    cand_options = [f"{i+1}. {c['display_name']}" for i, c in enumerate(candidates)]
    current_idx = st.session_state.get("selected_candidate_idx", 0)
    if current_idx >= len(cand_options):
        current_idx = 0

    st.sidebar.markdown("---")
    selected_cand_str = st.sidebar.selectbox(
        "📍 검색된 장소 목록 (원하는 주소 선택)",
        options=cand_options,
        index=current_idx,
        key="candidate_selectbox_widget"
    )
    selected_idx = cand_options.index(selected_cand_str)
    st.session_state["selected_candidate_idx"] = selected_idx
    selected_candidate = candidates[selected_idx]
else:
    st.sidebar.warning("⚠️ 검색된 위치가 없습니다. 다른 검색어를 입력해 보세요.")

# -----------------------------------------------------------------------------
# 6. 데이터 조회 및 메인 화면 (타이틀 및 지도)
# -----------------------------------------------------------------------------
if selected_candidate:
    prop_type = st.session_state.get("submitted_property_type", "아파트")
    months_opt = st.session_state.get("submitted_months", 12)
    period_str = f"최근 {months_opt//12}년"

    with st.spinner("🔄 해당 지역 실거래가 데이터 수집 및 위치 좌표 변환 중입니다..."):
        lat, lng, full_address, lawd_cd, region_list, filtered_df = fetch_real_estate_ultra_fast(
            selected_candidate['lat'],
            selected_candidate['lng'],
            selected_candidate['address'],
            selected_candidate['place_name'],
            prop_type,
            months_opt
        )

    if isinstance(region_list, list) and len(region_list) > 0:
        region_items_html = "".join([f"<li style='margin-bottom: 2px;'>{r}</li>" for r in region_list])
    else:
        region_items_html = f"<li>{region_list if region_list else '정보 없음'}</li>"

    st.sidebar.markdown(f"""
    <div class="info-banner-sidebar">
        📍 <b>선택 위치:</b> {full_address}<br>
        🏛️ <b>수집 지역:</b>
        <ul style="margin: 4px 0 8px 18px; padding-left: 0; list-style-type: disc;">
            {region_items_html}
        </ul>
        📊 <b>{period_str} [{prop_type}]</b> 실거래 총 <b>{len(filtered_df):,}건</b>
    </div>
    """, unsafe_allow_html=True)

    apt_summary_list = []
    trade_list = []

    if not filtered_df.empty:
        filtered_df['coord_key'] = filtered_df.apply(
            lambda r: f"{round(r['lat'], 4)},{round(r['lng'], 4)}", axis=1
        )

        rep_names = filtered_df.groupby('coord_key')['물건명'].agg(
            lambda x: x.value_counts().index[0]
        ).to_dict()

        filtered_df['통합물건명'] = filtered_df['coord_key'].map(rep_names)

        apt_grp = filtered_df.groupby('통합물건명').agg(
            평균매매가=('매매가(만원)', 'mean'),
            거래건수=('매매가(만원)', 'count'),
            lat=('lat', 'first'),
            lng=('lng', 'first')
        ).reset_index().rename(columns={'통합물건명': '물건명'})

        for _, r in apt_grp.iterrows():
            apt_summary_list.append({
                "apt_name": str(r['물건명']),
                "avg_price_fmt": format_korean_price(r['평균매매가']),
                "count": int(r['거래건수']),
                "lat": float(r['lat']),
                "lng": float(r['lng'])
            })

        for _, r in filtered_df.iterrows():
            trade_list.append({
                "apt_name": str(r['통합물건명']),
                "area": f"{float(r['면적(㎡)']):.1f}" if pd.notnull(r['면적(㎡)']) else "0.0",
                "floor": str(r['층수']),
                "price_raw": int(r['매매가(만원)']),
                "price_fmt": format_korean_price(r['매매가(만원)']),
                "deal_date": str(r['계약일'])
            })

    st.markdown("<h2 style='font-size: 21px; font-weight: 700; color: #f3f4f6; margin-bottom: 12px; margin-top: 0px;'>🗺️ 부동산 실거래가 시세 지도</h2>", unsafe_allow_html=True)

    kakao_map_component(
        key="kakao_map_comp",
        center_lat=lat,
        center_lng=lng,
        apt_summary=apt_summary_list,
        all_trades=trade_list
    )
