import os
import json
import requests
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components

# -----------------------------------------------------------------------------
# 0. 만원 단위 숫자를 'X억 Y만' 한글 단위로 변환하는 함수
# -----------------------------------------------------------------------------
def format_korean_price(price_manwon):
    if pd.isna(price_manwon) or price_manwon <= 0:
        return "0만"
    
    price = int(price_manwon)
    uk = price // 10000  # 억 단위
    man = price % 10000  # 만 단위
    
    if uk > 0 and man > 0:
        return f"{uk:,}억 {man:,}만"
    elif uk > 0:
        return f"{uk:,}억"
    else:
        return f"{man:,}만"

# -----------------------------------------------------------------------------
# 1. 페이지 기본 설정 및 CSS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="부동산 실거래가 지도", layout="wide")

st.markdown("""
<style>
    .info-banner {
        background-color: #f0fdf4;
        border: 2px solid #22c55e;
        border-radius: 10px;
        padding: 12px 18px;
        margin-bottom: 15px;
        font-size: 15px;
        font-weight: bold;
        color: #15803d;
    }

    [data-testid="stSidebar"] {
        min-width: 350px !important;
        max-width: 500px !important;
    }

    div[data-testid="stCheckbox"] {
        transform: scale(1.6) !important;
        transform-origin: left center !important;
        margin-top: 4px !important;
    }
</style>
""", unsafe_allow_html=True)

# Streamlit Secrets에서 API 키 로드
try:
    KAKAO_REST_KEY = st.secrets["KAKAO_REST_KEY"]
    MOLIT_SERVICE_KEY = st.secrets["MOLIT_SERVICE_KEY"]
    KAKAO_JS_KEY = st.secrets["KAKAO_JS_KEY"]
except Exception as e:
    st.error("⚠️ Streamlit Secrets에 API 키가 설정되지 않았습니다.")
    KAKAO_REST_KEY = ""
    MOLIT_SERVICE_KEY = ""
    KAKAO_JS_KEY = ""

# -----------------------------------------------------------------------------
# 2. 카카오 지도 커스텀 컴포넌트 HTML 생성
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
        html, body { width: 100%; height: 100%; margin: 0; padding: 0; background: transparent; overflow: hidden; }
        #map { width: 100%; height: 600px; border-radius: 10px; }
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map, clusterer, markers = [], overlays = [], circle;

        function sendToStreamlit(type, data) {
            var msg = Object.assign({ isStreamlitMessage: true, type: type }, data);
            window.parent.postMessage(msg, "*");
        }

        function selectApt(aptName) {
            sendToStreamlit("streamlit:setComponentValue", { 
                value: { apt_name: aptName, ts: Date.now() } 
            });
        }

        window.addEventListener("message", function(event) {
            if (event.data && event.data.type === "streamlit:render") {
                renderMap(event.data.args);
            }
        });

        sendToStreamlit("streamlit:componentReady", { apiVersion: 1 });
        sendToStreamlit("streamlit:setFrameHeight", { height: 655 });

        function renderMap(props) {
            var centerLat = props.center_lat;
            var centerLng = props.center_lng;
            var aptSummary = props.apt_summary;
            var radiusInfo = props.radius_info;

            kakao.maps.load(function() {
                var container = document.getElementById('map');
                if (!map) {
                    map = new kakao.maps.Map(container, {
                        center: new kakao.maps.LatLng(centerLat, centerLng),
                        level: 4
                    });
                } else {
                    map.setCenter(new kakao.maps.LatLng(centerLat, centerLng));
                }

                if (clusterer) clusterer.clear();
                markers.forEach(function(m) { m.setMap(null); });
                markers = [];
                overlays.forEach(function(o) { o.setMap(null); });
                overlays = [];
                if (circle) { circle.setMap(null); circle = null; }

                clusterer = new kakao.maps.MarkerClusterer({
                    map: map,
                    averageCenter: true,
                    minLevel: 5
                });

                if (radiusInfo && radiusInfo.use) {
                    circle = new kakao.maps.Circle({
                        center: new kakao.maps.LatLng(radiusInfo.lat, radiusInfo.lng),
                        radius: radiusInfo.radius * 1000,
                        strokeWeight: 2, strokeColor: '#FF0000', strokeOpacity: 0.8,
                        strokeStyle: 'solid', fillColor: '#FF0000', fillOpacity: 0.12
                    });
                    circle.setMap(map);
                }

                if (aptSummary && aptSummary.length > 0) {
                    aptSummary.forEach(function(item) {
                        var pos = new kakao.maps.LatLng(item.lat, item.lng);
                        var marker = new kakao.maps.Marker({ position: pos, clickable: true });

                        kakao.maps.event.addListener(marker, 'click', function() {
                            selectApt(item.apt_name);
                        });

                        var div = document.createElement('div');
                        div.style.cssText = 'cursor:pointer; padding:6px 10px; background:white; color:#2c3e50; border:2px solid #e74c3c; border-radius:10px; font-weight:bold; font-size:12px; box-shadow:0 2px 6px rgba(0,0,0,0.25); text-align:center; user-select:none;';
                        div.innerHTML = item.apt_name + '<br><span style="color:#e74c3c; font-size:13px;">평균 ' + item.avg_price_fmt + '</span> <span style="font-size:11px; color:#7f8c8d;">(' + item.count + '건)</span>';

                        div.addEventListener('click', function(e) {
                            e.stopPropagation();
                            selectApt(item.apt_name);
                        });

                        var overlay = new kakao.maps.CustomOverlay({
                            position: pos, clickable: true, content: div, yAnchor: 2.2
                        });
                        overlay.setMap(map);

                        markers.push(marker);
                        overlays.push(overlay);
                    });

                    clusterer.addMarkers(markers);
                }
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
# 3. 부동산 종류별 국토부 API 엔드포인트 매핑
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

# 📌 중심점 주변의 여러 행정구역(시/군/구 법정동 코드)을 탐색하는 함수
def get_nearby_lawd_codes(lat, lng, radius_km=2.0):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    lawd_info = {} # {lawd_cd: region_name}
    
    # 반경 오프셋 약 (1km = 약 0.009도)
    offset = (radius_km / 111.0)
    check_points = [
        (lat, lng), # 중심점
        (lat + offset, lng), # 북
        (lat - offset, lng), # 남
        (lat, lng + offset), # 동
        (lat, lng - offset)  # 서
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

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_real_estate_for_candidate(lat, lng, full_address, place_name, property_type, months_count, use_radius_limit, radius):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    display_addr = full_address if full_address else place_name

    # 📌 인근 구/시 법정동 코드 자동 추출 (구가 달라도 모두 수집)
    search_radius = radius if use_radius_limit else 2.5
    lawd_info = get_nearby_lawd_codes(lat, lng, radius_km=search_radius)
    
    if not lawd_info:
        return lat, lng, display_addr, "", "지역 정보 없음", pd.DataFrame()

    region_names_str = ", ".join(list(set(lawd_info.values())))
    months_list = get_recent_months(months_count)
    raw_items = []
    api_url = API_ENDPOINTS.get(property_type, API_ENDPOINTS["아파트"])
    
    # 여러 법정동(구)에 대해 각각 국토부 API 수집
    for lawd_cd in lawd_info.keys():
        for ymd in months_list:
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
                    res = requests.get(api_url, params=params, timeout=5)
                    if res.status_code == 200:
                        root = ET.fromstring(res.content)
                        items = root.findall('.//item')
                        if not items:
                            break
                        
                        for item in items:
                            if property_type == "아파트":
                                apt_name = item.findtext('aptNm', default='아파트').strip()
                            elif property_type == "연립/다세대":
                                apt_name = item.findtext('mhbNm', default='연립다세대').strip()
                            elif property_type == "오피스텔":
                                apt_name = item.findtext('offiNm', default='오피스텔').strip()
                            elif property_type == "단독/다가구":
                                apt_name = item.findtext('houseType', default='단독/다가구').strip()
                            elif property_type == "토지":
                                apt_name = f"토지({item.findtext('jimok', default='-').strip()})"
                            else:
                                apt_name = "부동산"

                            price_str = item.findtext('dealAmount', default='0').replace(',', '').strip()
                            
                            if property_type == "토지":
                                area_val = item.findtext('plottageArea', default='0')
                            elif property_type == "단독/다가구":
                                area_val = item.findtext('totalFloorArea', default='0')
                            else:
                                area_val = item.findtext('excluUseAr', default='0')
                            
                            area = float(area_val) if area_val else 0.0
                            umd_name = item.findtext('umdNm', default='').strip()
                            floor_val = item.findtext('floor', default='').strip()
                            
                            deal_year = item.findtext('dealYear', default='')
                            deal_month = item.findtext('dealMonth', default='').zfill(2)
                            deal_day = item.findtext('dealDay', default='').zfill(2)
                            
                            raw_items.append({
                                "apt_name": apt_name,
                                "umd_name": umd_name,
                                "price": int(price_str),
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

    coord_cache = {}
    valid_trades = []
    rad_limit = radius if use_radius_limit else 999.0

    # 📌 1단계: 전체 거래 중 '물건명(아파트명)'별로 대표 좌표를 딱 한 번씩만 구함 (속도 대폭 향상의 핵심!)
    unique_apt_names = set(item['apt_name'] for item in raw_items)
    
    for apt_name in unique_apt_names:
        c_lat, c_lng = None, None
        # 법정동명 조합 또는 아파트 이름으로 카카오 좌표 검색
        for query_str in [f"{region_names_str} {apt_name}", apt_name]:
            try:
                geo_url = f"https://dapi.kakao.com/v2/local/search/keyword.json?query={query_str}"
                geo_res = requests.get(geo_url, headers=headers, timeout=2).json()
                if geo_res.get('documents'):
                    doc = geo_res['documents'][0]
                    c_lat, c_lng = float(doc['y']), float(doc['x'])
                    break
            except Exception:
                pass
        
        # 좌표를 못 찾은 단지는 마커 쏠림 방지를 위해 스킵
        if c_lat is not None:
            c_dist = haversine_distance(lat, lng, c_lat, c_lng)
            coord_cache[apt_name] = (c_lat, c_lng, c_dist)

    # 📌 2단계: 미리 구해둔 좌표 캐시를 거래 내역에 빠르게 매핑
    for trade in raw_items:
        apt_name = trade['apt_name']
        if apt_name in coord_cache:
            c_lat, c_lng, c_dist = coord_cache[apt_name]
            
            if not use_radius_limit or c_dist <= rad_limit:
                trade_item = trade.copy()
                trade_item['lat'] = c_lat
                trade_item['lng'] = c_lng
                trade_item['거리(km)'] = c_dist
                valid_trades.append(trade_item)

    if not valid_trades:
        return lat, lng, display_addr, "", region_names_str, pd.DataFrame()

    df = pd.DataFrame(valid_trades)
    df = df.rename(columns={
        "apt_name": "물건명",
        "price": "매매가(만원)",
        "area": "면적(㎡)",
        "floor": "층수",
        "deal_date": "계약일"
    })
    df = df.sort_values(by=['계약일'], ascending=False).reset_index(drop=True)
    return lat, lng, display_addr, "", region_names_str, df

# -----------------------------------------------------------------------------
# 4. 중앙 정렬 HTML 표 출력 함수
# -----------------------------------------------------------------------------
def render_custom_centered_table(df):
    if df.empty:
        st.info("표시할 상세 데이터가 없습니다.")
        return

    html_lines = [
        "<style>",
        ".tbl-container { max-height: 470px; overflow-y: auto; border: 1px solid #374151; border-radius: 8px; margin-top: 8px; }",
        ".center-tbl { width: 100%; border-collapse: collapse; font-size: 14px; text-align: center; color: #f3f4f6; }",
        ".center-tbl th, .center-tbl td { padding: 9px 6px; text-align: center !important; vertical-align: middle !important; border-bottom: 1px solid #374151; }",
        ".center-tbl th { background-color: #1f2937; color: #ffffff; position: sticky; top: 0; z-index: 10; font-weight: bold; }",
        ".center-tbl tbody tr:nth-child(even) { background-color: #111827; }",
        ".center-tbl tbody tr:nth-child(odd) { background-color: #1f2937; }",
        ".center-tbl tbody tr:hover { background-color: #374151; }",
        "</style>",
        "<div class='tbl-container'>",
        "<table class='center-tbl'>",
        "<thead><tr><th style='width:8%;'>NO</th><th style='width:36%;'>물건명</th><th style='width:16%;'>면적(㎡)</th><th style='width:10%;'>층수</th><th style='width:15%;'>매매가</th><th style='width:15%;'>계약일</th></tr></thead>",
        "<tbody>"
    ]

    for idx, row in df.reset_index(drop=True).iterrows():
        p_val = format_korean_price(row['매매가(만원)'])
        a_val = f"{float(row['면적(㎡)']):.1f}" if pd.notnull(row['면적(㎡)']) else "0.0"
        
        html_lines.append(
            f"<tr><td>{idx+1}</td><td>{row['물건명']}</td><td>{a_val}</td><td>{row['층수']}</td><td>{p_val}</td><td>{row['계약일']}</td></tr>"
        )

    html_lines.append("</tbody></table></div>")
    st.markdown("\n".join(html_lines), unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 5. 세션 상태 초기화 및 콜백
# -----------------------------------------------------------------------------
def reset_filter_callback():
    st.session_state["select_apt_dropdown"] = "전체 보기"

if "select_apt_dropdown" not in st.session_state:
    st.session_state["select_apt_dropdown"] = "전체 보기"

if "last_click_ts" not in st.session_state:
    st.session_state["last_click_ts"] = None

# -----------------------------------------------------------------------------
# 6. 메인 UI 및 사이드바
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

    col_lbl1, col_sel1 = st.columns([1, 1])
    with col_lbl1:
        st.markdown("<p style='margin-top: 8px; font-weight: bold; font-size: 16px;'>조회 기간</p>", unsafe_allow_html=True)
    with col_sel1:
        months_count_input = st.selectbox(
            "조회 기간",
            options=[1, 3, 6, 12],
            index=3,
            format_func=lambda x: f"최근 {x}개월",
            label_visibility="collapsed"
        )

    st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)

    col_lbl2, col_chk2 = st.columns([1, 1])
    with col_lbl2:
        st.markdown("<p style='margin-top: 4px; font-weight: bold; font-size: 16px;'>검색 반경(km) 제한</p>", unsafe_allow_html=True)
    with col_chk2:
        use_radius_limit_input = st.checkbox("<p style='margin-top: 4px; font-weight: bold; font-size: 50px;'>", value=False, label_visibility="collapsed")
        
    radius_input = st.slider(
        "반경 범위 (km)", 
        min_value=0.5, 
        max_value=5.0, 
        value=1.5, 
        step=0.1,
        label_visibility="collapsed"
    )
    
    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)

    search_button = st.form_submit_button("🔍 위치 검색", use_container_width=True)

# 초기 접속 시 검색
if "candidates" not in st.session_state:
    initial_cands = search_location_candidates("호수로 688")
    st.session_state["candidates"] = initial_cands
    st.session_state["selected_candidate_idx"] = 0
    st.session_state["submitted_property_type"] = "아파트"
    st.session_state["submitted_months"] = 12
    st.session_state["submitted_use_radius"] = False
    st.session_state["submitted_radius"] = 1.5

if search_button:
    new_cands = search_location_candidates(search_query_input)
    st.session_state["candidates"] = new_cands
    st.session_state["selected_candidate_idx"] = 0
    st.session_state["select_apt_dropdown"] = "전체 보기"
    st.session_state["last_click_ts"] = None
    st.session_state["submitted_property_type"] = property_type_input
    st.session_state["submitted_months"] = months_count_input
    st.session_state["submitted_use_radius"] = use_radius_limit_input
    st.session_state["submitted_radius"] = radius_input

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
# 7. 선택된 후보 장소 기준 지도 및 실거래가 표현
# -----------------------------------------------------------------------------
if selected_candidate:
    prop_type = st.session_state.get("submitted_property_type", "아파트")
    months_opt = st.session_state.get("submitted_months", 12)
    period_str = f"최근 {months_opt}개월" if months_opt < 12 else "최근 1년"

    display_address_str = selected_candidate['address'] if selected_candidate['address'] else selected_candidate['place_name']
    spinner_message = f"⏳ [{display_address_str}]의 {period_str} [{prop_type}] 거래 정보를 수집 중입니다..."

    with st.spinner(spinner_message):
        lat, lng, full_address, lawd_cd, region_name, filtered_df = fetch_real_estate_for_candidate(
            selected_candidate['lat'],
            selected_candidate['lng'],
            selected_candidate['address'],
            selected_candidate['place_name'],
            prop_type,
            months_opt,
            st.session_state.get("submitted_use_radius", False),
            st.session_state.get("submitted_radius", 1.5)
        )

    apt_options = ["전체 보기"]
    if not filtered_df.empty:
        apt_options += sorted(list(filtered_df['물건명'].unique()))

    if st.session_state["select_apt_dropdown"] not in apt_options:
        st.session_state["select_apt_dropdown"] = "전체 보기"

    # 상단 요약 배너
    st.markdown(f"""
    <div class="info-banner">
        📍 <b>선택 위치:</b> {full_address}<br>
        ✅ <b>인근 수집 지역({region_name})</b>의 {period_str} <b>[{prop_type}]</b> 실거래가 총 <b>{len(filtered_df):,}건</b>을 불러왔습니다.
    </div>
    """, unsafe_allow_html=True)

    col_map, col_detail = st.columns([1, 1], gap="medium")

    # --- [좌측 : 카카오 지도] ---
    with col_map:
        st.subheader("🗺️ 부동산 실거래 지도")

        map_center_lat, map_center_lng = lat, lng
        current_selected = st.session_state["select_apt_dropdown"]
        if current_selected != "전체 보기" and not filtered_df.empty:
            match_row = filtered_df[filtered_df['물건명'] == current_selected]
            if not match_row.empty:
                map_center_lat = match_row['lat'].iloc[0]
                map_center_lng = match_row['lng'].iloc[0]

        apt_summary_list = []
        if not filtered_df.empty:
            apt_grp = filtered_df.groupby('물건명').agg(
                평균매매가=('매매가(만원)', 'mean'),
                거래건수=('매매가(만원)', 'count'),
                lat=('lat', 'first'),
                lng=('lng', 'first')
            ).reset_index()

            for _, r in apt_grp.iterrows():
                apt_summary_list.append({
                    "apt_name": r['물건명'],
                    "avg_price_fmt": format_korean_price(r['평균매매가']),
                    "count": int(r['거래건수']),
                    "lat": float(r['lat']),
                    "lng": float(r['lng'])
                })

        clicked_data = kakao_map_component(
            key="kakao_map_comp",
            center_lat=map_center_lat,
            center_lng=map_center_lng,
            apt_summary=apt_summary_list,
            radius_info={
                "use": st.session_state.get("submitted_use_radius", False),
                "radius": st.session_state.get("submitted_radius", 1.5),
                "lat": lat,
                "lng": lng
            }
        )

        if isinstance(clicked_data, dict):
            clicked_apt = clicked_data.get("apt_name")
            click_ts = clicked_data.get("ts")
            
            if click_ts and click_ts != st.session_state.get("last_click_ts"):
                st.session_state["last_click_ts"] = click_ts
                st.session_state["select_apt_dropdown"] = clicked_apt
                st.rerun()

    # --- [우측 : 물건 상세 내용] ---
    with col_detail:
        st.subheader("📊 물건 상세 내용")

        if not filtered_df.empty:
            st.markdown("<p style='font-weight: bold; margin-bottom: 5px; font-size: 15px;'>🔍 상세 검색 필터</p>", unsafe_allow_html=True)
            
            col_sel, col_btn = st.columns([4, 1])
            
            with col_sel:
                st.selectbox(
                    "물건 선택",
                    apt_options,
                    key="select_apt_dropdown",
                    label_visibility="collapsed"
                )

            with col_btn:
                st.button(
                    "🎛️ 필터해제", 
                    use_container_width=True, 
                    on_click=reset_filter_callback
                )

            active_apt = st.session_state["select_apt_dropdown"]

            if active_apt != "전체 보기":
                display_df = filtered_df[filtered_df['물건명'] == active_apt].copy()
                avg_price_str = format_korean_price(display_df['매매가(만원)'].mean()) if not display_df.empty else "0만"
                st.info(f"🏢 **{active_apt}** ({len(display_df)}건) | 💰 **평균 매매가:** {avg_price_str}")
            else:
                display_df = filtered_df.copy()
                st.caption("💡 지도의 마커를 클릭하면 해당 물건만 필터링됩니다.")

            render_custom_centered_table(display_df)
        else:
            st.info(f"해당 지역 및 조회 기간 내 [{prop_type}] 실거래가 데이터가 없습니다.")
