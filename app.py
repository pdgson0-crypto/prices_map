import os, json, requests, pandas as pd, numpy as np
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import streamlit as st
import streamlit.components.v1 as components

# -----------------------------------------------------------------------------
# 0. 유틸리티 함수 및 기본 설정
# -----------------------------------------------------------------------------
def format_korean_price(price):
    if pd.isna(price) or price <= 0: return "0만"
    p = int(price)
    uk, man = p // 10000, p % 10000
    if uk > 0 and man > 0: return f"{uk:,}억 {man:,}만"
    return f"{uk:,}억" if uk > 0 else f"{man:,}만"

def get_xml_text(item, tags, default=""):
    for tag in tags:
        val = item.findtext(tag)
        if val and val.strip(): return val.strip()
    return default

def haversine_distance(lat1, lon1, lat2, lon2):
    dlat, dlon = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dlat / 2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2)**2
    return 6371.0 * (2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))

def get_recent_months(n=12):
    today = datetime.now()
    months = []
    for i in range(n):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y:04d}{m:02d}")
    return months

def format_region_display(region_list):
    return list(dict.fromkeys(region_list))

st.set_page_config(page_title="PriceMap", layout="wide")

st.markdown("""
<style>
    div[data-testid="stSpinner"] {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        background: rgba(17, 24, 39, 0.95) !important;
        z-index: 999999 !important;
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
    }
    div[data-testid="stSpinner"] > div {
        background: transparent !important;
        color: #ffffff !important;
        font-size: 20px !important;
        font-weight: bold !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 12px !important;
    }
    .info-banner-sidebar { 
        background: #064e3b; 
        border: 1px solid #10b981; 
        border-radius: 8px; 
        padding: 12px 14px; 
        margin-top: 12px; 
        font-size: 13px; 
        color: #ecfdf5; 
        line-height: 1.6; 
    }
    [data-testid="stSidebar"] { min-width: 340px !important; max-width: 450px !important; }
    [data-testid="stSidebarCollapseButton"], [data-testid="collapsedControl"], header[data-testid="stHeader"] { display: none !important; }
    .block-container { padding-top: 0.5rem !important; padding-bottom: 0 !important; padding-left: 0.5rem !important; padding-right: 0.5rem !important; max-width: 100% !important; }
    
    div[data-testid="stCustomComponentV1"],
    div[data-testid="stCustomComponentV1"] > iframe,
    iframe[title="kakao_map_comp.kakao_map_comp"] {
        height: calc(100vh - 1.5rem) !important;
        min-height: 650px !important;
        width: 100% !important;
    }
</style>
""", unsafe_allow_html=True)

try:
    KAKAO_REST_KEY = st.secrets["KAKAO_REST_KEY"]
    MOLIT_SERVICE_KEY = st.secrets["MOLIT_SERVICE_KEY"]
    KAKAO_JS_KEY = st.secrets["KAKAO_JS_KEY"]
except Exception:
    st.error("⚠️ Streamlit Secrets 키를 설정해주세요.")

# -----------------------------------------------------------------------------
# 1. 카카오 지도 컴포넌트 파일 생성
# -----------------------------------------------------------------------------
MAP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "kakao_map_comp"))
os.makedirs(MAP_DIR, exist_ok=True)
INDEX_HTML_PATH = os.path.join(MAP_DIR, "index.html")

INDEX_HTML_CONTENT = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey={KAKAO_JS_KEY}&libraries=clusterer&autoload=false"></script>
    <style>
        html, body {{ width: 100%; height: 100vh; margin: 0; padding: 0; overflow: hidden; font-family: sans-serif; background: #111827; }}
        #map-container {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
        #loading-overlay {{
            position: absolute; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(17, 24, 39, 0.95); z-index: 99999;
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            color: #ffffff; text-align: center; box-sizing: border-box; padding: 20px;
        }}
        .spinner-icon {{
            width: 42px; height: 42px; border: 4px solid #374151;
            border-top: 4px solid #60a5fa; border-radius: 50%;
            animation: spin 0.9s linear infinite; margin-bottom: 16px;
        }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        #detail-panel {{ position: absolute; top: 0; right: -430px; width: 410px; height: 100%; background: #111827; color: #f3f4f6; transition: right 0.3s; z-index: 80000; display: flex; flex-direction: column; border-left: 1px solid #374151; }}
        #detail-panel.open {{ right: 0; }}
        .panel-header {{ padding: 16px; background: #1f2937; border-bottom: 1px solid #374151; display: flex; justify-content: space-between; }}
        .panel-title {{ font-size: 17px; font-weight: bold; color: #60a5fa; }}
        .close-btn {{ background: #374151; border: none; color: #9ca3af; border-radius: 50%; width: 28px; height: 28px; cursor: pointer; }}
        .close-btn:hover {{ background: #ef4444; color: #fff; }}
        .panel-body {{ flex: 1; overflow-y: auto; padding: 12px; }}
        .panel-table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: center; }}
        .panel-table th, .panel-table td {{ padding: 10px 4px; border-bottom: 1px solid #374151; }}
        .panel-table th {{ background: #1f2937; color: #d1d5db; position: sticky; top: 0; }}
        .custom-overlay-card {{
            cursor: pointer; padding: 6px 10px; background: white; color: #2c3e50;
            border: 2px solid #e74c3c; border-radius: 10px; font-weight: bold; font-size: 12px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.25); text-align: center;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}
        .custom-overlay-card.highlight-target {{
            background: #fef08a !important; border: 2.5px solid #d97706 !important;
            color: #0f172a !important;
        }}
        .custom-overlay-card.hover-active {{
            transform: scale(1.1);
            box-shadow: 0 6px 16px rgba(0,0,0,0.45) !important;
            border-color: #2563eb !important;
        }}
    </style>
</head>
<body>
    <div id="map-container">
        <div id="loading-overlay">
            <div class="spinner-icon"></div>
            <div style="font-size: 20px; font-weight: bold; color: #f3f4f6;">지도 오버레이 렌더링 중...</div>
        </div>
        <div id="map" style="width: 100%; height: 100%;"></div>
        <div id="detail-panel">
            <div class="panel-header">
                <div><div id="panel-title" class="panel-title">물건 정보</div><div id="panel-sub" style="font-size:13px; color:#9ca3af;">거래 0건</div></div>
                <button class="close-btn" onclick="closePanel()">✕</button>
            </div>
            <div class="panel-body">
                <table class="panel-table">
                    <thead><tr><th style="width:12%;">NO</th><th style="width:22%;">면적</th><th style="width:18%;">층</th><th style="width:26%;">매매가</th><th style="width:22%;">계약일</th></tr></thead>
                    <tbody id="panel-table-body"></tbody>
                </table>
            </div>
        </div>
    </div>
    <script>
        var map, clusterer, markers = [], overlays = [], allTradeData = [];
        function sendMsg(type, data) {{ window.parent.postMessage(Object.assign({{ isStreamlitMessage: true, type: type }}, data), "*"); }}
        function closePanel() {{ document.getElementById('detail-panel').classList.remove('open'); }}
        function notifyHeight() {{
            var h = 850;
            try {{
                if (window.top && window.top.innerHeight) h = window.top.innerHeight - 30;
                else if (window.parent && window.parent.innerHeight) h = window.parent.innerHeight - 30;
            }} catch(e) {{ h = Math.max(window.innerHeight, 850); }}
            sendMsg("streamlit:setFrameHeight", {{ height: Math.max(h, 650) }});
            if (map) map.relayout();
        }}
        window.addEventListener("resize", notifyHeight);
        function openPanel(aptName) {{
            var panel = document.getElementById('detail-panel'), trades = allTradeData.filter(function(d) {{ return d.apt_name === aptName; }});
            if (!trades.length) return closePanel();
            document.getElementById('panel-title').innerText = aptName;
            var sum = trades.reduce(function(a, b) {{ return a + b.price_raw; }}, 0), avg = Math.round(sum / trades.length);
            document.getElementById('panel-sub').innerHTML = '총 <b style="color:#60a5fa;">' + trades.length + '</b>건 | 평균 <b style="color:#f87171;">' + formatPrice(avg) + '</b>';
            var tbody = document.getElementById('panel-table-body');
            tbody.innerHTML = '';
            trades.forEach(function(t, i) {{
                tbody.innerHTML += '<tr><td>' + (i+1) + '</td><td>' + t.area + '㎡</td><td>' + t.floor + '</td><td style="color:#f87171;font-weight:bold;">' + t.price_fmt + '</td><td>' + t.deal_date + '</td></tr>';
            }});
            panel.classList.add('open');
        }}
        function formatPrice(p) {{
            if (!p || p <= 0) return '0만';
            var u = Math.floor(p / 10000), m = p % 10000;
            return (u > 0 ? u.toLocaleString() + '억 ' : '') + (m > 0 || u === 0 ? m.toLocaleString() + '만' : '');
        }}
        window.addEventListener("message", function(e) {{ if (e.data && e.data.type === "streamlit:render") renderMap(e.data.args); }});
        sendMsg("streamlit:componentReady", {{ apiVersion: 1 }});
        notifyHeight();

        function renderMap(props) {{
            var loader = document.getElementById('loading-overlay');
            if (loader) loader.style.display = 'flex';
            kakao.maps.load(function() {{
                try {{
                    var container = document.getElementById('map');
                    var pos = new kakao.maps.LatLng(props.center_lat, props.center_lng);
                    if (!map) map = new kakao.maps.Map(container, {{ center: pos, level: 3 }});
                    else {{ map.setCenter(pos); map.setLevel(3); map.relayout(); }}

                    if (clusterer) clusterer.clear();
                    markers.forEach(function(m) {{ m.setMap(null); }});
                    overlays.forEach(function(o) {{ o.setMap(null); }});
                    markers = []; overlays = [];
                    allTradeData = props.all_trades || [];

                    if (!clusterer) clusterer = new kakao.maps.MarkerClusterer({{ map: map, averageCenter: true, minLevel: 5 }});

                    (props.apt_summary || []).forEach(function(item) {{
                        var p = new kakao.maps.LatLng(item.lat, item.lng);
                        var m = new kakao.maps.Marker({{ position: p, clickable: true }});
                        var baseZIndex = item.is_target ? 500 : 10;
                        var div = document.createElement('div');
                        div.className = 'custom-overlay-card' + (item.is_target ? ' highlight-target' : '');
                        div.innerHTML = item.apt_name + '<br><span style="color:' + (item.is_target ? '#b45309' : '#e74c3c') + ';">평균 ' + item.avg_price_fmt + '</span> <span style="font-size:11px;color:#64748b;">(' + item.count + '건)</span>';

                        var o = new kakao.maps.CustomOverlay({{ position: p, clickable: true, content: div, yAnchor: 2.2, zIndex: baseZIndex }});
                        div.onmouseenter = function() {{ o.setZIndex(9999); div.style.zIndex = "9999"; div.classList.add('hover-active'); }};
                        div.onmouseleave = function() {{ o.setZIndex(baseZIndex); div.style.zIndex = baseZIndex; div.classList.remove('hover-active'); }};
                        div.onclick = function(e) {{ e.stopPropagation(); map.panTo(p); openPanel(item.apt_name); }};
                        kakao.maps.event.addListener(m, 'click', function() {{ map.panTo(p); openPanel(item.apt_name); }});

                        m.setMap(map); o.setMap(map);
                        markers.push(m); overlays.push(o);
                    }});
                    if (clusterer && markers.length > 0) clusterer.addMarkers(markers);
                    closePanel();
                }} catch(e) {{ console.error("Map Error:", e); }}
                finally {{ if (loader) loader.style.display = 'none'; notifyHeight(); }}
            }});
        }}
    </script>
</body>
</html>"""

# 매번 쓰지 않고 내용 변경 시에만 업데이트
if not os.path.exists(INDEX_HTML_PATH) or open(INDEX_HTML_PATH, 'r', encoding='utf-8').read() != INDEX_HTML_CONTENT:
    with open(INDEX_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(INDEX_HTML_CONTENT)

kakao_map_component = components.declare_component("kakao_map_comp", path=MAP_DIR)

# -----------------------------------------------------------------------------
# 2. 데이터 수집 API 및 캐싱 최적화 영역
# -----------------------------------------------------------------------------
API_ENDPOINTS = {
    "아파트": "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "연립/다세대": "http://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    "단독/다가구": "http://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",
    "오피스텔": "http://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
    "토지": "http://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"
}

def get_nearby_lawd_codes(lat, lng, radius_km=2.5):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    lawd_info = {}
    offset = radius_km / 111.0
    for c_lat, c_lng in [(lat, lng), (lat+offset, lng), (lat-offset, lng), (lat, lng+offset), (lat, lng-offset)]:
        try:
            res = requests.get(f"https://dapi.kakao.com/v2/local/geo/coord2regioncode.json?x={c_lng}&y={c_lat}", headers=headers, timeout=3).json()
            if res.get('documents'):
                d = res['documents'][0]
                code_5 = d['code'][:5]
                region_2 = d.get('region_2depth_name', '').strip()
                region_1 = d.get('region_1depth_name', '').strip()
                lawd_info[code_5] = region_2 if region_2 else region_1
        except Exception: pass
    return lawd_info

def search_location_candidates(query):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    candidates, seen = [], set()
    for endpoint in ["keyword", "address"]:
        try:
            res = requests.get(f"https://dapi.kakao.com/v2/local/search/{endpoint}.json?query={query}&size=10", headers=headers, timeout=3).json()
            for doc in res.get('documents', []):
                name = doc.get('place_name', doc.get('address_name', '')).strip()
                addr = doc.get('road_address_name', doc.get('address_name', '')).strip()
                if addr not in seen:
                    seen.add(addr)
                    candidates.append({'display_name': f"{name} ({addr})" if name != addr else addr, 'place_name': name, 'address': addr, 'lat': float(doc['y']), 'lng': float(doc['x'])})
        except Exception: pass
    return candidates

# 단일 월 데이터 조회 기본 함수
def fetch_molit_core(lawd_cd, ymd, property_type):
    api_url = API_ENDPOINTS.get(property_type, API_ENDPOINTS["아파트"])
    items_list = []
    page_no = 1
    clean_key = requests.utils.unquote(MOLIT_SERVICE_KEY)
    
    while True:
        params = {'serviceKey': clean_key, 'LAWD_CD': lawd_cd, 'DEAL_YMD': ymd, 'pageNo': str(page_no), 'numOfRows': '100'}
        try:
            res = requests.get(api_url, params=params, timeout=5)
            if res.status_code == 200:
                root = ET.fromstring(res.content)
                header_code = root.findtext('.//resultCode') or root.findtext('.//header/resultCode')
                if header_code and header_code not in ['00', '000']: break

                # 실거래 해제(취소)된 건 제외하는 로직 반영
                items = root.findall('.//item')
                if not items: break
                
                for item in items:
                    c_deal = get_xml_text(item, ['cdealDay', 'cancelDealDay'])
                    if c_deal: continue # 취소된 거래는 수집 스킵

                    umd_name = get_xml_text(item, ['umdNm', 'umdName', 'dong'])
                    jibun_val = get_xml_text(item, ['jibun', 'lnbr'])

                    if property_type == "연립/다세대":
                        apt_name = get_xml_text(item, ['mhbNm', 'mhbName', 'rhNm', 'vesselNm', 'buildingNm'])
                        area_val = get_xml_text(item, ['myeonArea', 'excluUseAr', 'excluArea', 'area'])
                    elif property_type == "아파트":
                        apt_name = get_xml_text(item, ['aptNm', 'aptName'])
                        area_val = get_xml_text(item, ['excluUseAr', 'excluArea', 'area'])
                    elif property_type == "오피스텔":
                        apt_name = get_xml_text(item, ['offiNm', 'offiName', 'aptNm'])
                        area_val = get_xml_text(item, ['excluUseAr', 'excluArea', 'area'])
                    elif property_type == "토지":
                        apt_name = ""
                        area_val = get_xml_text(item, ['dealArea', 'plottageArea', 'totArea', 'myeonArea', 'area'])
                    else:
                        apt_name = ""
                        area_val = get_xml_text(item, ['totalFloorArea', 'totArea', 'plottageArea', 'area'])

                    raw_apt_name = apt_name
                    if not apt_name or not apt_name.strip():
                        if property_type == "토지":
                            jimok_val = get_xml_text(item, ['jimok'])
                            suffix = f" ({jimok_val})" if jimok_val else " (토지)"
                            apt_name = f"{umd_name} {jibun_val}{suffix}" if umd_name and jibun_val else "토지 매매"
                        elif property_type == "단독/다가구":
                            apt_name = f"{umd_name} {jibun_val} (단독/다가구)" if umd_name and jibun_val else "단독/다가구"
                        else:
                            apt_name = f"{umd_name} {jibun_val} 빌라" if umd_name and jibun_val else "연립다세대"

                    price_str = get_xml_text(item, ['dealAmount', 'dealAmountManwon'], default='0').replace(',', '').strip()
                    area = float(area_val) if area_val and area_val != '0' else 0.0
                    floor_val = get_xml_text(item, ['floor'])
                    
                    deal_year = get_xml_text(item, ['dealYear', 'year'])
                    deal_month = get_xml_text(item, ['dealMonth', 'month']).zfill(2)
                    deal_day = get_xml_text(item, ['dealDay', 'day']).zfill(2)
                    
                    items_list.append({
                        "apt_name": apt_name, "raw_apt_name": raw_apt_name, "umd_name": umd_name, "jibun": jibun_val,
                        "price": int(price_str) if price_str.isdigit() else 0, "area": area,
                        "floor": f"{floor_val}층" if (floor_val and property_type != "토지") else "-",
                        "deal_date": f"{deal_year}-{deal_month}-{deal_day}"
                    })
                
                total_cnt_elem = root.find('.//totalCount') or root.find('.//body/totalCount')
                total_count = int(total_cnt_elem.text) if total_cnt_elem is not None and total_cnt_elem.text.isdigit() else 0
                if total_count > 0 and (page_no * 100) >= total_count: break
                if len(items) < 100: break
                page_no += 1
            else: break
        except Exception: break

    return items_list

# ① 과거월 데이터: ttl=None (무기한 영구 메모리 캐시)
@st.cache_data(ttl=None, show_spinner=False)
def fetch_past_molit_task(lawd_cd, ymd, property_type):
    return fetch_molit_core(lawd_cd, ymd, property_type)

# ② 최근월 데이터(당월 및 전월): ttl=3600 (1시간 주기로 최신 반영)
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recent_molit_task(lawd_cd, ymd, property_type):
    return fetch_molit_core(lawd_cd, ymd, property_type)

# ③ 카카오 지오코딩 좌표: ttl=None (무기한 영구 메모리 캐시)
@st.cache_data(ttl=None, show_spinner=False)
def get_cached_apt_coord(region_name, umd_name, jibun, raw_apt_name):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    if umd_name and jibun and jibun.strip():
        query = f"{umd_name} {jibun}".strip()
        try:
            res = requests.get(f"https://dapi.kakao.com/v2/local/search/address.json?query={query}", headers=headers, timeout=2).json()
            if res.get('documents'):
                doc = res['documents'][0]
                return float(doc['y']), float(doc['x'])
        except Exception: pass

    if raw_apt_name and raw_apt_name.strip():
        query = f"{umd_name} {raw_apt_name}".strip()
        try:
            res = requests.get(f"https://dapi.kakao.com/v2/local/search/keyword.json?query={query}", headers=headers, timeout=2).json()
            if res.get('documents'):
                for doc in res['documents']:
                    addr = doc.get('address_name', '') or doc.get('road_address_name', '')
                    if umd_name in addr:
                        return float(doc['y']), float(doc['x'])
        except Exception: pass

    return None, None

def fetch_real_estate_ultra_fast(lat, lng, full_address, place_name, property_type, months_count):
    lawd_info = get_nearby_lawd_codes(lat, lng, radius_km=1.5)
    if not lawd_info:
        return lat, lng, full_address, "", [], pd.DataFrame()

    region_list = sorted(list(set(lawd_info.values())))
    all_months = get_recent_months(months_count)
    
    # 최근 2개월(당월, 지난달)은 주기적 갱신 대상, 그 이전은 영구 보관 대상
    recent_months = set(all_months[:2])
    
    tasks = []
    for code in lawd_info.keys():
        for ymd in all_months:
            is_recent = ymd in recent_months
            tasks.append((code, ymd, property_type, is_recent))

    raw_items = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for c, y, p, is_recent in tasks:
            if is_recent:
                futures.append(executor.submit(fetch_recent_molit_task, c, y, p))
            else:
                futures.append(executor.submit(fetch_past_molit_task, c, y, p))
                
        for f in as_completed(futures):
            res = f.result()
            if res: raw_items.extend(res)

    if not raw_items:
        return lat, lng, full_address, "", region_list, pd.DataFrame()

    # 영구 캐시 처리된 지오코딩 함수 병열 호출
    unique_locs = {(i['umd_name'], i['jibun'], i['apt_name'], i.get('raw_apt_name', '')): i for i in raw_items}
    coord_cache = {}
    main_region = region_list[0] if region_list else ""

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_cached_apt_coord, main_region, k[0], k[1], k[3]): k for k in unique_locs.keys()}
        for f in as_completed(futures):
            k = futures[f]
            c_lat, c_lng = f.result()
            if c_lat is not None and c_lng is not None:
                coord_cache[k] = (c_lat, c_lng, haversine_distance(lat, lng, c_lat, c_lng))

    valid_trades = []
    for t in raw_items:
        k = (t['umd_name'], t['jibun'], t['apt_name'], t.get('raw_apt_name', ''))
        if k in coord_cache:
            c_lat, c_lng, dist = coord_cache[k]
            t_item = t.copy()
            t_item.update({'lat': c_lat, 'lng': c_lng, '거리(km)': dist})
            valid_trades.append(t_item)

    if not valid_trades:
        return lat, lng, full_address, "", region_list, pd.DataFrame()

    df = pd.DataFrame(valid_trades).rename(columns={"apt_name": "물건명", "price": "매매가(만원)", "area": "면적(㎡)", "floor": "층수", "deal_date": "계약일"})
    return lat, lng, full_address, "", region_list, df.sort_values(by=['계약일'], ascending=False).reset_index(drop=True)

# -----------------------------------------------------------------------------
# 3. 메인 실행 영역
# -----------------------------------------------------------------------------
st.sidebar.markdown("<h3 style='font-size: 24px; font-weight: bold; margin-bottom: 0px;'>🏢 한국자산관리아카데미</h3>", unsafe_allow_html=True)
st.sidebar.title("📍 주소 및 조건")

with st.sidebar.form(key="search_form"):
    search_query_input = st.text_input("검색할 주소/건물명", value="호수로 688")
    property_type_input = st.selectbox("부동산 유형", options=["아파트", "연립/다세대", "단독/다가구", "오피스텔", "토지"], index=0)
    months_count_input = st.selectbox("조회 기간", options=[12, 24, 36], index=0, format_func=lambda x: f"최근 {x//12}년")
    search_button = st.form_submit_button("🔍 위치 검색", use_container_width=True)

if "candidates" not in st.session_state or search_button:
    q = search_query_input if search_button else "호수로 688"
    st.session_state["candidates"] = search_location_candidates(q)
    st.session_state["selected_candidate_idx"] = 0
    st.session_state["submitted_property_type"] = property_type_input if search_button else "아파트"
    st.session_state["submitted_months"] = months_count_input if search_button else 12

candidates = st.session_state.get("candidates", [])
if candidates:
    cand_options = [f"{i+1}. {c['display_name']}" for i, c in enumerate(candidates)]
    st.sidebar.markdown("---")
    selected_cand_str = st.sidebar.selectbox("📍 검색된 장소 목록", options=cand_options, index=0)
    selected_candidate = candidates[cand_options.index(selected_cand_str)]

    prop_type = st.session_state.get("submitted_property_type", "아파트")
    months_opt = st.session_state.get("submitted_months", 12)

    with st.spinner("인근 지역 실거래가 데이터 수집중입니다..."):
        lat, lng, full_address, _, region_list, filtered_df = fetch_real_estate_ultra_fast(
            selected_candidate['lat'], selected_candidate['lng'], selected_candidate['address'], selected_candidate['place_name'], prop_type, months_opt
        )

    display_regions = format_region_display(region_list)
    region_bullets_html = "".join([f"<div style='margin-left: 10px;'>• {r}</div>" for r in display_regions])

    st.sidebar.markdown(f"""
    <div class="info-banner-sidebar">
        📍 <b>검색 위치 :</b> {full_address}<br>
        🏛️ <b>수집 지역</b>
        {region_bullets_html}
        📊 <b>[{prop_type}] 최근 {months_opt//12}년 실거래 총 {len(filtered_df):,}건</b>
    </div>
    """, unsafe_allow_html=True)

    apt_summary_list, trade_list = [], []
    if not filtered_df.empty:
        filtered_df['coord_key'] = filtered_df.apply(lambda r: f"{round(r['lat'], 4)},{round(r['lng'], 4)}", axis=1)
        rep_names = filtered_df.groupby('coord_key')['물건명'].agg(lambda x: x.value_counts().index[0]).to_dict()
        filtered_df['통합물건명'] = filtered_df['coord_key'].map(rep_names)

        apt_grp = filtered_df.groupby('통합물건명').agg(평균매매가=('매매가(만원)', 'mean'), 거래건수=('매매가(만원)', 'count'), lat=('lat', 'first'), lng=('lng', 'first')).reset_index()
        
        target_lat, target_lng = selected_candidate['lat'], selected_candidate['lng']
        apt_grp['target_dist'] = apt_grp.apply(lambda r: haversine_distance(target_lat, target_lng, r['lat'], r['lng']), axis=1)

        nearby = apt_grp[apt_grp['target_dist'] <= 0.2]
        target_apt_name = nearby.sort_values(by='target_dist').iloc[0]['통합물건명'] if not nearby.empty else None

        for _, r in apt_grp.iterrows():
            item_apt_name = str(r['통합물건명']).strip()
            is_target = (target_apt_name is not None and item_apt_name == target_apt_name)

            apt_summary_list.append({
                "apt_name": item_apt_name,
                "avg_price_fmt": format_korean_price(r['평균매매가']),
                "count": int(r['거래건수']),
                "lat": float(r['lat']),
                "lng": float(r['lng']),
                "is_target": is_target
            })

        for _, r in filtered_df.iterrows():
            trade_list.append({
                "apt_name": str(r['통합물건명']),
                "area": f"{float(r['면적(㎡)']):.1f}",
                "floor": str(r['층수']),
                "price_raw": int(r['매매가(만원)']),
                "price_fmt": format_korean_price(r['매매가(만원)']),
                "deal_date": str(r['계약일'])
            })

    kakao_map_component(
        key="kakao_map_comp",
        center_lat=lat,
        center_lng=lng,
        search_address=selected_candidate['place_name'] or selected_candidate['address'],
        apt_summary=apt_summary_list,
        all_trades=trade_list
    )
else:
    st.sidebar.warning("⚠️ 검색된 위치가 없습니다.")
