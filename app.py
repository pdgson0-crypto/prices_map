import os
import requests
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import textwrap

import streamlit as st
import streamlit.components.v1 as components

# =============================================================================
# 0. 기본 설정
# =============================================================================
st.set_page_config(
    page_title="부동산 실거래가 지도",
    layout="wide"
)

CACHE_TTL = 60 * 60 * 24          # 24시간 캐시
SEARCH_RADIUS_KM = 2.5            # 검색 반경 (km)
RECENT_MONTHS = 12                # 최근 12개월
API_MAX_WORKERS = 8               # 국토부 API 동시 호출 수
GEOCODE_MAX_WORKERS = 8           # 카카오 지오코딩 동시 호출 수

# =============================================================================
# 1. 스타일 (CSS)
# =============================================================================
st.markdown(
    """
    <style>
        .info-box {
            background-color: #f0fdf4;
            border: 2px solid #22c55e;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            color: #15803d;
            font-size: 15px;
            line-height: 1.6;
        }
        [data-testid="stSidebar"] {
            min-width: 350px !important;
            max-width: 500px !important;
        }
    </style>
    """,
    unsafe_allow_html=True
)

# =============================================================================
# 2. API 키 설정
# =============================================================================
try:
    KAKAO_REST_KEY = st.secrets["KAKAO_REST_KEY"]
    MOLIT_SERVICE_KEY = st.secrets["MOLIT_SERVICE_KEY"]
    KAKAO_JS_KEY = st.secrets["KAKAO_JS_KEY"]
except Exception:
    st.error("⚠️ Streamlit Secrets에 KAKAO_REST_KEY, MOLIT_SERVICE_KEY, KAKAO_JS_KEY를 설정해주세요.")
    KAKAO_REST_KEY = ""
    MOLIT_SERVICE_KEY = ""
    KAKAO_JS_KEY = ""

# =============================================================================
# 3. 유틸리티 함수
# =============================================================================
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

def calculate_distance(lat1, lon1, lat2, lon2):
    """Haversine formula를 이용한 두 지점 간 거리 계산 (km)"""
    R = 6371.0
    lat1_rad, lon1_rad = np.radians(lat1), np.radians(lon1)
    lat2_rad, lon2_rad = np.radians(lat2), np.radians(lon2)
    
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    
    a = np.sin(dlat / 2)**2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c

# =============================================================================
# 4. 국토부 API 엔드포인트
# =============================================================================
API_ENDPOINTS = {
    "아파트": "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "연립/다세대": "http://apis.data.go.kr/1613000/RTMSDataSvcRHTradeDev/getRTMSDataSvcRHTradeDev",
    "단독/다가구": "http://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",
    "오피스텔": "http://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
}

# =============================================================================
# 5. 카카오 주소 검색 및 지오코딩 (캐시 적용)
# =============================================================================
@st.cache_data(ttl=CACHE_TTL)
def search_address_kakao(query):
    if not KAKAO_REST_KEY or not query:
        return []
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    params = {"query": query}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        if res.status_code == 200:
            docs = res.json().get("documents", [])
            results = []
            for doc in docs:
                results.append({
                    "address_name": doc.get("address_name"),
                    "lat": float(doc.get("y")),
                    "lng": float(doc.get("x")),
                })
            return results
    except Exception:
        pass
    return []

@st.cache_data(ttl=CACHE_TTL)
def get_lawd_cd_by_coords(lat, lng):
    """좌표 기반 법정동/행정동 API로 시군구 코드(5자리 LAWD_CD) 추출"""
    if not KAKAO_REST_KEY:
        return None, ""
    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    params = {"x": lng, "y": lat}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        if res.status_code == 200:
            docs = res.json().get("documents", [])
            for doc in docs:
                if doc.get("region_type") in ["B", "H"]:
                    code = doc.get("code")
                    if code and len(code) >= 5:
                        lawd_cd = code[:5]
                        full_name = f"{doc.get('region_1depth_name')} {doc.get('region_2depth_name')}"
                        return lawd_cd, full_name
    except Exception:
        pass
    return None, ""

@st.cache_data(ttl=CACHE_TTL)
def geocode_building(address_str):
    """개별 건물/주소 좌표 변환 (주소 검색 + 키워드 2차 보완)"""
    if not KAKAO_REST_KEY or not address_str:
        return None, None
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    params = {"query": address_str}
    
    # 1. 일반 주소 검색
    try:
        url_addr = "https://dapi.kakao.com/v2/local/search/address.json"
        res = requests.get(url_addr, headers=headers, params=params, timeout=3)
        if res.status_code == 200:
            docs = res.json().get("documents", [])
            if docs:
                return float(docs[0].get("y")), float(docs[0].get("x"))
                
        # 2. 주소 검색 실패 시 키워드 검색 보완
        url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
        res_kw = requests.get(url_kw, headers=headers, params=params, timeout=3)
        if res_kw.status_code == 200:
            docs_kw = res_kw.json().get("documents", [])
            if docs_kw:
                return float(docs_kw[0].get("y")), float(docs_kw[0].get("x"))
    except Exception:
        pass
    return None, None

# =============================================================================
# 6. 국토부 실거래가 API 수집 (병렬 처리 + 캐시)
# =============================================================================
@st.cache_data(ttl=CACHE_TTL)
def fetch_month_api_cached(endpoint, lawd_cd, ymd):
    if not MOLIT_SERVICE_KEY:
        return []
    
    params = {
        "serviceKey": MOLIT_SERVICE_KEY,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": ymd,
        "numOfRows": "1000"
    }
    
    try:
        response = requests.get(endpoint, params=params, timeout=10)
        if response.status_code != 200:
            return []
        
        root = ET.fromstring(response.content)
        items = root.findall(".//item")
        rows = []
        for item in items:
            row = {}
            for child in item:
                row[child.tag] = child.text
            rows.append(row)
        return rows
    except Exception:
        return []

def fetch_molit_data_parallel(property_type, lawd_cd, months_list):
    endpoint = API_ENDPOINTS.get(property_type)
    if not endpoint:
        return pd.DataFrame()
        
    all_rows = []
    with ThreadPoolExecutor(max_workers=API_MAX_WORKERS) as executor:
        future_to_ymd = {
            executor.submit(fetch_month_api_cached, endpoint, lawd_cd, ymd): ymd 
            for ymd in months_list
        }
        for future in as_completed(future_to_ymd):
            try:
                data = future.result()
                if data:
                    all_rows.extend(data)
            except Exception:
                pass
                
    if not all_rows:
        return pd.DataFrame()
        
    return pd.DataFrame(all_rows)

# =============================================================================
# 7. 데이터 정제 및 유연한 XML 태그 파싱
# =============================================================================
def extract_val(row_dict, possible_keys, default=""):
    for k in possible_keys:
        if k in row_dict and row_dict[k] is not None:
            val = str(row_dict[k]).strip()
            if val:
                return val
    return default

def parse_and_clean_data(df, property_type, lawd_name, center_lat, center_lng, radius_km):
    if df.empty:
        return pd.DataFrame()

    cleaned_records = []
    for _, row in df.iterrows():
        r = row.to_dict()
        
        # 한글/영문 XML 태그 수용
        name = extract_val(r, ["aptNm", "mhlmNm", "offiNm", "아파트", "연립다세대", "오피스텔", "건물명"], "건물")
        price_raw = extract_val(r, ["dealAmount", "거래금액"], "0")
        umd = extract_val(r, ["umdNm", "법정동", "법정동명", "동"], "")
        jibun = extract_val(r, ["jibun", "지번"], "")
        year = extract_val(r, ["dealYear", "년"], "")
        month = extract_val(r, ["dealMonth", "월"], "")
        day = extract_val(r, ["dealDay", "일"], "")
        floor = extract_val(r, ["floor", "층"], "")

        price_clean = float(price_raw.replace(",", "").strip()) if price_raw else 0.0

        date_str = ""
        if year and month and day:
            date_str = f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 정밀 지오코딩 주소 조합
        search_addr = f"{lawd_name} {umd} {jibun}".strip()
        if not jibun:
            search_addr = f"{lawd_name} {umd} {name}".strip()

        cleaned_records.append({
            "name": name,
            "price_clean": price_clean,
            "umd": umd,
            "jibun": jibun,
            "date": date_str,
            "floor": floor,
            "search_addr": search_addr
        })

    cdf = pd.DataFrame(cleaned_records)
    if cdf.empty:
        return pd.DataFrame()

    # 중복 주소 기준 그룹 지오코딩 (속도 최적화)
    unique_addrs = cdf["search_addr"].unique()
    coord_cache = {}

    def fetch_coord(addr):
        lat, lng = geocode_building(addr)
        return addr, lat, lng

    with ThreadPoolExecutor(max_workers=GEOCODE_MAX_WORKERS) as executor:
        future_to_addr = {executor.submit(fetch_coord, addr): addr for addr in unique_addrs}
        for future in as_completed(future_to_addr):
            try:
                addr, lat, lng = future.result()
                if lat and lng:
                    coord_cache[addr] = (lat, lng)
            except Exception:
                pass

    lat_list = []
    lng_list = []
    for addr in cdf["search_addr"]:
        if addr in coord_cache:
            lat_list.append(coord_cache[addr][0])
            lng_list.append(coord_cache[addr][1])
        else:
            lat_list.append(np.nan)
            lng_list.append(np.nan)

    cdf["lat"] = lat_list
    cdf["lng"] = lng_list

    cdf = cdf.dropna(subset=["lat", "lng"])
    if cdf.empty:
        return pd.DataFrame()

    # 반경 내 거리 필터링
    distances = calculate_distance(center_lat, center_lng, cdf["lat"].values, cdf["lng"].values)
    cdf["distance_km"] = distances
    cdf = cdf[cdf["distance_km"] <= radius_km]

    return cdf.sort_values(by="date", ascending=False)

# =============================================================================
# 8. Streamlit UI
# =============================================================================
st.title("🏡 대한민국 부동산 실거래가 분석 지도")

with st.sidebar:
    st.header("🔍 검색 및 필터 설정")
    search_query = st.text_input("기준 주소 또는 지역 검색", value="고양시 일산동구 호수로 688")
    property_type = st.selectbox("부동산 유형", ["아파트", "연립/다세대", "단독/다가구", "오피스텔"])
    search_button = st.button("실거래가 조회 실행", type="primary", use_container_width=True)

# 1. 기준 주소 검색
address_results = search_address_kakao(search_query)
if not address_results:
    st.warning("입력한 주소의 위치를 찾을 수 없습니다. 정확한 주소를 입력해주세요.")
    st.stop()

selected_loc = address_results[0]
center_lat = selected_loc["lat"]
center_lng = selected_loc["lng"]
full_addr_name = selected_loc["address_name"]

# 2. 시군구 코드 추출
lawd_cd, lawd_name = get_lawd_cd_by_coords(center_lat, center_lng)
if not lawd_cd:
    st.error("해당 위치의 시군구 코드를 가져오지 못했습니다.")
    st.stop()

# 3. 최근 12개월 연월(YMD) 생성
now = datetime.now()
months_list = []
for i in range(RECENT_MONTHS):
    y = now.year
    m = now.month - i
    while m <= 0:
        m += 12
        y -= 1
    months_list.append(f"{y}{m:02d}")

# 4. 데이터 수집 및 정제
with st.spinner(f"[{lawd_name}] 지역의 최근 {RECENT_MONTHS}개월 {property_type} 실거래 데이터를 수집 중입니다..."):
    raw_df = fetch_molit_data_parallel(property_type, lawd_cd, months_list)

with st.spinner("수집된 데이터를 정제 및 위치 계산 중입니다..."):
    df = parse_and_clean_data(raw_df, property_type, lawd_name, center_lat, center_lng, SEARCH_RADIUS_KM)

# 5. 상단 정보 배너 (textwrap.dedent로 HTML 파싱 오류 방지)
info_html = textwrap.dedent(f"""
<div class="info-box">
    📍 <b>선택 위치:</b> {full_addr_name}<br>
    🧭 <b>검색 반경:</b> {SEARCH_RADIUS_KM}km &nbsp;|&nbsp; 🏢 <b>조회 지역:</b> {lawd_name} ({lawd_cd})<br>
    📅 <b>조회 기간:</b> 최근 {RECENT_MONTHS}개월 &nbsp;|&nbsp; 📊 <b>실거래 건수:</b> 총 <b>{len(df):,}건</b>
</div>
""")
st.markdown(info_html, unsafe_allow_html=True)

# 6. 지도 및 데이터표 배치
col1, col2 = st.columns([1.2, 1])

with col1:
    st.subheader("🗺️ 부동산 실거래가 지도")
    if not df.empty:
        markers_data = []
        for _, row in df.iterrows():
            markers_data.append({
                "lat": row["lat"],
                "lng": row["lng"],
                "name": str(row["name"]),
                "price": format_korean_price(row["price_clean"]),
                "date": str(row["date"])
            })
            
        kakao_map_html = textwrap.dedent(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Kakao Map</title>
            <style>
                #map {{ width: 100%; height: 500px; border-radius: 10px; }}
                .custom-overlay {{
                    background: white; border: 1px solid #333; padding: 4px 8px;
                    font-size: 11px; font-weight: bold; border-radius: 4px; box-shadow: 0px 2px 4px rgba(0,0,0,0.2);
                    color: #111;
                }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script type="text/javascript" src="//dapi.kakao.com/v2/maps/sdk.js?appkey={KAKAO_JS_KEY}"></script>
            <script>
                var mapContainer = document.getElementById('map'),
                    mapOption = {{
                        center: new kakao.maps.LatLng({center_lat}, {center_lng}),
                        level: 4
                    }};
                var map = new kakao.maps.Map(mapContainer, mapOption);

                var centerMarker = new kakao.maps.Marker({{
                    position: new kakao.maps.LatLng({center_lat}, {center_lng}),
                    map: map
                }});

                var markers = {markers_data};

                markers.forEach(function(item) {{
                    var markerPosition = new kakao.maps.LatLng(item.lat, item.lng);
                    var marker = new kakao.maps.Marker({{
                        position: markerPosition,
                        map: map
                    }});

                    var content = '<div class="custom-overlay">' + item.name + '<br><span style="color:#d97706;">' + item.price + '</span></div>';
                    var customOverlay = new kakao.maps.CustomOverlay({{
                        position: markerPosition,
                        content: content,
                        yAnchor: 1.6
                    }});
                    customOverlay.setMap(map);
                }});
            </script>
        </body>
        </html>
        """)
        components.html(kakao_map_html, height=520)
    else:
        st.info("표시할 실거래가 데이터가 없습니다.")

with col2:
    st.subheader("📋 물건 상세 내용")
    if not df.empty:
        display_df = df[["date", "name", "price_clean", "floor", "distance_km"]].copy()
        display_df["가격"] = display_df["price_clean"].apply(format_korean_price)
        display_df = display_df.rename(columns={
            "date": "거래일자",
            "name": "물건명",
            "floor": "층",
            "distance_km": "거리(km)"
        })
        display_df["거리(km)"] = display_df["거리(km)"].round(2)
        st.dataframe(display_df[["거래일자", "물건명", "가격", "층", "거리(km)"]], use_container_width=True, height=500)
    else:
        st.info(f"해당 검색 위치 반경 {SEARCH_RADIUS_KM}km 내 최근 {RECENT_MONTHS}개월 [{property_type}] 실거래가 데이터가 없습니다.")
