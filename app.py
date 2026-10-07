import os
import requests
import pandas as pd
import numpy as np
import xml.etree.ElementTree as ET

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
import streamlit.components.v1 as components


# =============================================================================
# 0. 기본 설정
# =============================================================================

st.set_page_config(
    page_title="부동산 실거래가 지도",
    layout="wide"
)

CACHE_TTL = 60 * 60 * 24          # 24시간
SEARCH_RADIUS_KM = 2.5            # 검색 주소 반경
RECENT_MONTHS = 12                # 최근 12개월
API_MAX_WORKERS = 8               # 국토부 API 동시 호출 수
GEOCODE_MAX_WORKERS = 8           # 카카오 지오코딩 동시 호출 수


# =============================================================================
# 1. CSS
# =============================================================================

st.markdown(
    """
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
    </style>
    """,
    unsafe_allow_html=True
)


# =============================================================================
# 2. API KEY
# =============================================================================

try:
    KAKAO_REST_KEY = st.secrets["KAKAO_REST_KEY"]
    MOLIT_SERVICE_KEY = st.secrets["MOLIT_SERVICE_KEY"]
    KAKAO_JS_KEY = st.secrets["KAKAO_JS_KEY"]

except Exception:
    st.error(
        "⚠️ Streamlit Secrets에 KAKAO_REST_KEY / "
        "MOLIT_SERVICE_KEY / KAKAO_JS_KEY를 설정해주세요."
    )

    KAKAO_REST_KEY = ""
    MOLIT_SERVICE_KEY = ""
    KAKAO_JS_KEY = ""


# =============================================================================
# 3. 가격 포맷
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


# =============================================================================
# 4. 부동산 유형별 API
# =============================================================================

API_ENDPOINTS = {

    "아파트":
        "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",

    "연립/다세대":
        "http://apis.data.go.kr/1613000/RTMSDataSvcRHTradeDev/getRTMSDataSvcRHTradeDev",

    "단독/다가구":
        "http://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",

    "오피스텔":
        "http://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",

    "토지":
        "http://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"
}


# =============================================================================
# 5. 거리 계산
# =============================================================================

def haversine_distance(lat1, lon1, lat2, lon2):

    R = 6371.0

    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)

    a = (
        np.sin(dlat / 2) ** 2
        +
        np.cos(np.radians(lat1))
        *
        np.cos(np.radians(lat2))
        *
        np.sin(dlon / 2) ** 2
    )

    c = 2 * np.arctan2(
        np.sqrt(a),
        np.sqrt(1 - a)
    )

    return R * c


# =============================================================================
# 6. 최근 12개월
# =============================================================================

def get_recent_months(n=12):

    today = datetime.now()

    months = []

    for i in range(n):

        year = today.year
        month = today.month - i

        while month <= 0:
            month += 12
            year -= 1

        months.append(
            f"{year:04d}{month:02d}"
        )

    return months


# =============================================================================
# 7. 카카오 좌표 → 법정동
#
# 검색 위치가 속한 법정동 + 주변 법정동을 찾는다.
# =============================================================================

@st.cache_data(
    ttl=CACHE_TTL,
    max_entries=5000,
    show_spinner=False
)
def get_region_info(lat, lng):

    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_KEY}"
    }

    try:

        url = (
            "https://dapi.kakao.com/v2/local/"
            "geo/coord2regioncode.json"
        )

        response = requests.get(
            url,
            params={
                "x": lng,
                "y": lat
            },
            headers=headers,
            timeout=5
        )

        response.raise_for_status()

        documents = response.json().get(
            "documents",
            []
        )

        if not documents:
            return {}

        # B: 법정동
        for doc in documents:

            if doc.get("region_type") == "B":

                return {
                    "code": doc.get("code", ""),
                    "lawd_cd": doc.get("code", "")[:5],
                    "sido": doc.get("region_1depth_name", ""),
                    "sigungu": doc.get("region_2depth_name", ""),
                    "emd": doc.get("region_3depth_name", ""),
                    "full_name": (
                        f"{doc.get('region_1depth_name', '')} "
                        f"{doc.get('region_2depth_name', '')} "
                        f"{doc.get('region_3depth_name', '')}"
                    ).strip()
                }

        return {}

    except Exception:
        return {}


# =============================================================================
# 8. 주변 읍면동 찾기
#
# 카카오 coord2regioncode API를 검색 위치 주변에 격자로 호출한다.
# 읍면동의 정확한 경계 자체를 API가 제공하는 것은 아니므로,
# 여러 지점을 샘플링하여 주변 법정동을 확보한다.
# =============================================================================

@st.cache_data(
    ttl=CACHE_TTL,
    max_entries=3000,
    show_spinner=False
)
def get_nearby_emd_regions(lat, lng, radius_km=2.5):

    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_KEY}"
    }

    regions = {}

    # 위도 1도 ≈ 111km
    lat_offset = radius_km / 111.0

    # 경도는 위도에 따라 길이가 달라짐
    cos_lat = max(
        np.cos(np.radians(lat)),
        0.2
    )

    lng_offset = radius_km / (
        111.0 * cos_lat
    )

    # 중심 + 주변 8방향 + 중간 지점
    points = [
        (lat, lng),

        (lat + lat_offset, lng),
        (lat - lat_offset, lng),

        (lat, lng + lng_offset),
        (lat, lng - lng_offset),

        (lat + lat_offset * 0.7,
         lng + lng_offset * 0.7),

        (lat + lat_offset * 0.7,
         lng - lng_offset * 0.7),

        (lat - lat_offset * 0.7,
         lng + lng_offset * 0.7),

        (lat - lat_offset * 0.7,
         lng - lng_offset * 0.7),

        # 중간 샘플
        (lat + lat_offset * 0.4, lng),
        (lat - lat_offset * 0.4, lng),
        (lat, lng + lng_offset * 0.4),
        (lat, lng - lng_offset * 0.4)
    ]

    for c_lat, c_lng in points:

        try:

            url = (
                "https://dapi.kakao.com/v2/local/"
                "geo/coord2regioncode.json"
            )

            response = requests.get(
                url,
                params={
                    "x": c_lng,
                    "y": c_lat
                },
                headers=headers,
                timeout=4
            )

            if response.status_code != 200:
                continue

            documents = response.json().get(
                "documents",
                []
            )

            for doc in documents:

                if doc.get("region_type") != "B":
                    continue

                code = doc.get("code", "")

                if not code:
                    continue

                emd_code = code[:8]

                regions[emd_code] = {
                    "code": code,
                    "emd_code": emd_code,
                    "lawd_cd": code[:5],
                    "sido": doc.get(
                        "region_1depth_name",
                        ""
                    ),
                    "sigungu": doc.get(
                        "region_2depth_name",
                        ""
                    ),
                    "emd": doc.get(
                        "region_3depth_name",
                        ""
                    ),
                    "full_name": (
                        f"{doc.get('region_1depth_name', '')} "
                        f"{doc.get('region_2depth_name', '')} "
                        f"{doc.get('region_3depth_name', '')}"
                    ).strip()
                }

        except Exception:
            continue

    return list(regions.values())


# =============================================================================
# 9. 주소 / 건물 검색
# =============================================================================

@st.cache_data(
    ttl=CACHE_TTL,
    max_entries=3000,
    show_spinner=False
)
def search_location_candidates(query):

    headers = {
        "Authorization": f"KakaoAK {KAKAO_REST_KEY}"
    }

    candidates = []

    seen = set()

    # -------------------------------------------------------------------------
    # 키워드 검색
    # -------------------------------------------------------------------------

    try:

        response = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            params={
                "query": query,
                "size": 15
            },
            headers=headers,
            timeout=5
        )

        if response.status_code == 200:

            for doc in response.json().get(
                "documents",
                []
            ):

                name = doc.get(
                    "place_name",
                    ""
                ).strip()

                road_addr = doc.get(
                    "road_address_name",
                    ""
                ).strip()

                jibun_addr = doc.get(
                    "address_name",
                    ""
                ).strip()

                address = (
                    road_addr
                    if road_addr
                    else jibun_addr
                )

                try:
                    lat = float(doc["y"])
                    lng = float(doc["x"])
                except Exception:
                    continue

                key = address or name

                if key in seen:
                    continue

                seen.add(key)

                display_name = (
                    f"{name} ({address})"
                    if address and name != address
                    else (
                        address
                        if address
                        else name
                    )
                )

                candidates.append({
                    "display_name": display_name,
                    "place_name": name,
                    "address": address,
                    "lat": lat,
                    "lng": lng
                })

    except Exception:
        pass

    # -------------------------------------------------------------------------
    # 주소 검색
    # -------------------------------------------------------------------------

    try:

        response = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            params={
                "query": query,
                "size": 10
            },
            headers=headers,
            timeout=5
        )

        if response.status_code == 200:

            for doc in response.json().get(
                "documents",
                []
            ):

                address = doc.get(
                    "address_name",
                    ""
                ).strip()

                if not address or address in seen:
                    continue

                try:
                    lat = float(doc["y"])
                    lng = float(doc["x"])
                except Exception:
                    continue

                seen.add(address)

                candidates.append({
                    "display_name": f"[주소] {address}",
                    "place_name": address,
                    "address": address,
                    "lat": lat,
                    "lng": lng
                })

    except Exception:
        pass

    return candidates


# =============================================================================
# 10. 국토부 API 1회 호출
#
# 중요:
# 이 함수에는 cache를 붙이지 않는다.
# 아래 fetch_month_api_cached()에서 월별 결과를 cache한다.
# =============================================================================

def fetch_single_api_request(
    api_url,
    lawd_cd,
    ymd,
    property_type
):

    page_no = 1

    local_items = []

    while True:

        params = {
            "serviceKey":
                requests.utils.unquote(
                    MOLIT_SERVICE_KEY
                ),

            "LAWD_CD": lawd_cd,
            "DEAL_YMD": ymd,
            "pageNo": str(page_no),
            "numOfRows": "1000"
        }

        try:

            response = requests.get(
                api_url,
                params=params,
                timeout=10
            )

            if response.status_code != 200:
                break

            root = ET.fromstring(
                response.content
            )

            result_code = root.findtext(
                ".//resultCode"
            )

            if (
                result_code
                and result_code not in [
                    "00",
                    "INFO-000"
                ]
            ):
                break

            items = root.findall(
                ".//item"
            )

            if not items:
                break

            for item in items:

                # -------------------------------------------------------------
                # 물건명
                # -------------------------------------------------------------

                if property_type == "아파트":

                    name_val = item.findtext(
                        "aptNm",
                        default=""
                    ).strip()

                elif property_type == "연립/다세대":

                    name_val = (
                        item.findtext(
                            "mblNm",
                            default=""
                        )
                        or
                        item.findtext(
                            "buildNm",
                            default=""
                        )
                    ).strip()

                elif property_type == "오피스텔":

                    name_val = (
                        item.findtext(
                            "offiNm",
                            default=""
                        )
                        or
                        item.findtext(
                            "buildNm",
                            default=""
                        )
                    ).strip()

                elif property_type == "단독/다가구":

                    name_val = (
                        item.findtext(
                            "houseType",
                            default=""
                        )
                        or
                        item.findtext(
                            "buildNm",
                            default=""
                        )
                    ).strip()

                elif property_type == "토지":

                    jimok = item.findtext(
                        "jimok",
                        default=""
                    ).strip()

                    name_val = (
                        f"토지({jimok})"
                        if jimok
                        else "토지"
                    )

                else:

                    name_val = ""

                # -------------------------------------------------------------
                # 주소
                # -------------------------------------------------------------

                jibun = item.findtext(
                    "jibun",
                    default=""
                ).strip()

                umd_name = item.findtext(
                    "umdNm",
                    default=""
                ).strip()

                if not name_val:

                    name_val = (
                        f"{umd_name} {jibun}".strip()
                        if jibun
                        else property_type
                    )

                # -------------------------------------------------------------
                # 가격
                # -------------------------------------------------------------

                price_str = (
                    item.findtext(
                        "dealAmount",
                        default="0"
                    )
                    .replace(",", "")
                    .strip()
                )

                # -------------------------------------------------------------
                # 면적
                # -------------------------------------------------------------

                if property_type == "토지":

                    area_val = item.findtext(
                        "plottageArea",
                        default="0"
                    )

                elif property_type == "단독/다가구":

                    area_val = item.findtext(
                        "totalFloorArea",
                        default="0"
                    )

                else:

                    area_val = item.findtext(
                        "excluUseAr",
                        default="0"
                    )

                try:
                    area = float(area_val)
                except Exception:
                    area = 0.0

                # -------------------------------------------------------------
                # 층
                # -------------------------------------------------------------

                floor_val = item.findtext(
                    "floor",
                    default=""
                ).strip()

                # -------------------------------------------------------------
                # 계약일
                # -------------------------------------------------------------

                deal_year = item.findtext(
                    "dealYear",
                    default=""
                )

                deal_month = item.findtext(
                    "dealMonth",
                    default=""
                ).zfill(2)

                deal_day = item.findtext(
                    "dealDay",
                    default=""
                ).zfill(2)

                # -------------------------------------------------------------
                # 결과
                # -------------------------------------------------------------

                local_items.append({

                    "apt_name":
                        name_val,

                    "umd_name":
                        umd_name,

                    "jibun":
                        jibun,

                    "price":
                        int(price_str)
                        if price_str.isdigit()
                        else 0,

                    "area":
                        area,

                    "floor":
                        (
                            f"{floor_val}층"
                            if floor_val
                            else "-"
                        ),

                    "deal_date":
                        f"{deal_year}-"
                        f"{deal_month}-"
                        f"{deal_day}",

                    "lawd_cd":
                        lawd_cd
                })

            if len(items) < 1000:
                break

            page_no += 1

        except Exception:
            break

    return local_items


# =============================================================================
# 11. 국토부 월별 데이터 캐시
#
# ★ 핵심 캐시
#
# 같은 시군구 + 같은 월 + 같은 부동산 유형이면
# 24시간 동안 국토부 API를 다시 호출하지 않는다.
# =============================================================================

@st.cache_data(
    ttl=CACHE_TTL,
    max_entries=3000,
    show_spinner=False
)
def fetch_month_api_cached(
    lawd_cd,
    ymd,
    property_type
):

    api_url = API_ENDPOINTS.get(
        property_type
    )

    if not api_url:
        return []

    return fetch_single_api_request(
        api_url,
        lawd_cd,
        ymd,
        property_type
    )


# =============================================================================
# 12. 여러 월 / 시군구 병렬 수집
# =============================================================================

def fetch_molit_data_parallel(
    lawd_codes,
    property_type
):

    months = get_recent_months(
        RECENT_MONTHS
    )

    tasks = []

    for lawd_cd in lawd_codes:

        for ymd in months:

            tasks.append(
                (
                    lawd_cd,
                    ymd,
                    property_type
                )
            )

    raw_items = []

    # -------------------------------------------------------------------------
    # 병렬 처리
    # -------------------------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=API_MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_month_api_cached,
                lawd_cd,
                ymd,
                property_type
            ):
                (
                    lawd_cd,
                    ymd
                )

            for lawd_cd, ymd, property_type
            in tasks
        }

        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

                if result:
                    raw_items.extend(
                        result
                    )

            except Exception:
                pass

    return raw_items


# =============================================================================
# 13. 거래 데이터 → 읍면동 1차 필터
#
# API에서 시군구 전체를 가져온 뒤
# umdNm으로 주변 읍면동만 남긴다.
# =============================================================================

def filter_by_emd(
    raw_items,
    nearby_regions
):

    if not raw_items:
        return []

    allowed_emd = {
        region["emd"]
        for region in nearby_regions
        if region.get("emd")
    }

    if not allowed_emd:
        return raw_items

    filtered = [
        item
        for item in raw_items
        if item.get("umd_name")
        in allowed_emd
    ]

    return filtered


# =============================================================================
# 14. 카카오 건물 지오코딩
#
# ★ 24시간 캐시
# ★ 같은 건물은 한 번만 검색
# =============================================================================

@st.cache_data(
    ttl=CACHE_TTL,
    max_entries=20000,
    show_spinner=False
)
def geocode_building(
    region_name,
    umd_name,
    apt_name,
    jibun
):

    headers = {
        "Authorization":
            f"KakaoAK {KAKAO_REST_KEY}"
    }

    queries = []

    # 1순위
    if region_name and umd_name and apt_name:

        queries.append(
            f"{region_name} "
            f"{umd_name} "
            f"{apt_name}"
        )

    # 2순위
    if region_name and apt_name:

        queries.append(
            f"{region_name} "
            f"{apt_name}"
        )

    # 3순위
    if apt_name:

        queries.append(
            apt_name
        )

    # 4순위
    if region_name and umd_name and jibun:

        queries.append(
            f"{region_name} "
            f"{umd_name} "
            f"{jibun}"
        )

    seen = set()

    for query in queries:

        if not query or query in seen:
            continue

        seen.add(query)

        # -------------------------------------------------------------
        # 키워드 검색
        # -------------------------------------------------------------

        try:

            response = requests.get(
                "https://dapi.kakao.com/v2/local/search/keyword.json",
                params={
                    "query": query,
                    "size": 5
                },
                headers=headers,
                timeout=4
            )

            if response.status_code == 200:

                docs = response.json().get(
                    "documents",
                    []
                )

                if docs:

                    doc = docs[0]

                    return (
                        float(doc["y"]),
                        float(doc["x"])
                    )

        except Exception:
            pass

        # -------------------------------------------------------------
        # 주소 검색
        # -------------------------------------------------------------

        try:

            response = requests.get(
                "https://dapi.kakao.com/v2/local/search/address.json",
                params={
                    "query": query,
                    "size": 5
                },
                headers=headers,
                timeout=4
            )

            if response.status_code == 200:

                docs = response.json().get(
                    "documents",
                    []
                )

                if docs:

                    doc = docs[0]

                    return (
                        float(doc["y"]),
                        float(doc["x"])
                    )

        except Exception:
            pass

    return None, None


# =============================================================================
# 15. 거래 데이터 지오코딩
#
# 같은 건물명을 중복 지오코딩하지 않는다.
# =============================================================================

def attach_coordinates(
    items,
    selected_lat,
    selected_lng,
    region_name
):

    if not items:
        return []

    # -------------------------------------------------------------------------
    # 고유 건물 키
    # -------------------------------------------------------------------------

    unique_keys = {}

    for item in items:

        key = (
            item.get("umd_name", ""),
            item.get("apt_name", ""),
            item.get("jibun", "")
        )

        unique_keys[key] = item

    # -------------------------------------------------------------------------
    # 지오코딩
    # -------------------------------------------------------------------------

    coord_cache = {}

    with ThreadPoolExecutor(
        max_workers=GEOCODE_MAX_WORKERS
    ) as executor:

        futures = {}

        for key, item in unique_keys.items():

            umd_name = item.get(
                "umd_name",
                ""
            )

            apt_name = item.get(
                "apt_name",
                ""
            )

            jibun = item.get(
                "jibun",
                ""
            )

            futures[
                executor.submit(
                    geocode_building,
                    region_name,
                    umd_name,
                    apt_name,
                    jibun
                )
            ] = key

        for future in as_completed(
            futures
        ):

            key = futures[future]

            try:
                coord_cache[key] = (
                    future.result()
                )
            except Exception:
                coord_cache[key] = (
                    None,
                    None
                )

    # -------------------------------------------------------------------------
    # 좌표 + 실제 거리
    # -------------------------------------------------------------------------

    valid_items = []

    for item in items:

        key = (
            item.get("umd_name", ""),
            item.get("apt_name", ""),
            item.get("jibun", "")
        )

        c_lat, c_lng = coord_cache.get(
            key,
            (None, None)
        )

        if c_lat is None:
            continue

        distance = haversine_distance(
            selected_lat,
            selected_lng,
            c_lat,
            c_lng
        )

        # 실제 거리 기준 최종 필터
        if distance <= SEARCH_RADIUS_KM:

            new_item = item.copy()

            new_item["lat"] = c_lat
            new_item["lng"] = c_lng
            new_item["거리(km)"] = round(
                distance,
                3
            )

            valid_items.append(
                new_item
            )

    return valid_items


# =============================================================================
# 16. 전체 실거래 데이터 수집
# =============================================================================

@st.cache_data(
    ttl=CACHE_TTL,
    max_entries=1000,
    show_spinner=False
)
def fetch_real_estate_for_candidate(
    lat,
    lng,
    full_address,
    place_name,
    property_type
):

    display_addr = (
        full_address
        if full_address
        else place_name
    )

    # -------------------------------------------------------------------------
    # 1. 검색 위치의 법정동
    # -------------------------------------------------------------------------

    region_info = get_region_info(
        lat,
        lng
    )

    if not region_info:

        return (
            lat,
            lng,
            display_addr,
            "",
            "지역 정보 없음",
            pd.DataFrame()
        )

    # -------------------------------------------------------------------------
    # 2. 검색 위치 반경 2.5km 읍면동
    # -------------------------------------------------------------------------

    nearby_regions = get_nearby_emd_regions(
        lat,
        lng,
        SEARCH_RADIUS_KM
    )

    # 검색 중심 읍면동은 무조건 포함
    center_emd = region_info.get(
        "emd"
    )

    if center_emd:

        center_exists = any(
            r.get("emd") == center_emd
            for r in nearby_regions
        )

        if not center_exists:

            nearby_regions.append(
                region_info
            )

    # -------------------------------------------------------------------------
    # 3. 읍면동이 속한 시군구 코드
    # -------------------------------------------------------------------------

    lawd_codes = sorted({
        r["lawd_cd"]
        for r in nearby_regions
        if r.get("lawd_cd")
    })

    if not lawd_codes:

        lawd_codes = [
            region_info["lawd_cd"]
        ]

    # -------------------------------------------------------------------------
    # 4. 국토부 API
    #
    # 시군구 × 12개월
    # -------------------------------------------------------------------------

    raw_items = fetch_molit_data_parallel(
        lawd_codes,
        property_type
    )

    if not raw_items:

        region_name = region_info.get(
            "full_name",
            ""
        )

        return (
            lat,
            lng,
            display_addr,
            "",
            region_name,
            pd.DataFrame()
        )

    # -------------------------------------------------------------------------
    # 5. 읍면동 1차 필터
    # -------------------------------------------------------------------------

    emd_filtered = filter_by_emd(
        raw_items,
        nearby_regions
    )

    # -------------------------------------------------------------------------
    # 6. 중복 제거
    # -------------------------------------------------------------------------

    if emd_filtered:

        unique_items = {}

        for item in emd_filtered:

            key = (
                item.get("lawd_cd"),
                item.get("umd_name"),
                item.get("jibun"),
                item.get("apt_name"),
                item.get("deal_date"),
                item.get("price"),
                item.get("area")
            )

            unique_items[key] = item

        emd_filtered = list(
            unique_items.values()
        )

    # -------------------------------------------------------------------------
    # 7. 건물 좌표 + 실제 2.5km 거리 필터
    # -------------------------------------------------------------------------

    region_name = (
        f"{region_info.get('sido', '')} "
        f"{region_info.get('sigungu', '')}"
    ).strip()

    valid_trades = attach_coordinates(
        emd_filtered,
        lat,
        lng,
        region_name
    )

    if not valid_trades:

        return (
            lat,
            lng,
            display_addr,
            "",
            region_name,
            pd.DataFrame()
        )

    # -------------------------------------------------------------------------
    # 8. DataFrame
    # -------------------------------------------------------------------------

    df = pd.DataFrame(
        valid_trades
    )

    df = df.rename(
        columns={
            "apt_name":
                "물건명",

            "price":
                "매매가(만원)",

            "area":
                "면적(㎡)",

            "floor":
                "층수",

            "deal_date":
                "계약일",

            "umd_name":
                "읍면동"
        }
    )

    df = df.sort_values(
        by=[
            "계약일",
            "거리(km)"
        ],
        ascending=[
            False,
            True
        ]
    ).reset_index(
        drop=True
    )

    return (
        lat,
        lng,
        display_addr,
        ",".join(lawd_codes),
        region_name,
        df
    )


# =============================================================================
# 17. 상세 테이블
# =============================================================================

def render_custom_centered_table(df):

    if df.empty:

        st.info(
            "표시할 상세 데이터가 없습니다."
        )

        return

    html_lines = [

        "<style>",

        ".tbl-container { "
        "max-height: 470px; "
        "overflow-y: auto; "
        "border: 1px solid #374151; "
        "border-radius: 8px; "
        "margin-top: 8px; "
        "}",

        ".center-tbl { "
        "width: 100%; "
        "border-collapse: collapse; "
        "font-size: 14px; "
        "text-align: center; "
        "color: #f3f4f6; "
        "}",

        ".center-tbl th, "
        ".center-tbl td { "
        "padding: 9px 6px; "
        "text-align: center !important; "
        "vertical-align: middle !important; "
        "border-bottom: 1px solid #374151; "
        "}",

        ".center-tbl th { "
        "background-color: #1f2937; "
        "color: #ffffff; "
        "position: sticky; "
        "top: 0; "
        "z-index: 10; "
        "font-weight: bold; "
        "}",

        ".center-tbl tbody tr:nth-child(even) "
        "{ background-color: #111827; }",

        ".center-tbl tbody tr:nth-child(odd) "
        "{ background-color: #1f2937; }",

        ".center-tbl tbody tr:hover "
        "{ background-color: #374151; }",

        "</style>",

        "<div class='tbl-container'>",

        "<table class='center-tbl'>",

        "<thead>",
        "<tr>",
        "<th style='width:7%;'>NO</th>",
        "<th style='width:28%;'>물건명</th>",
        "<th style='width:13%;'>읍면동</th>",
        "<th style='width:13%;'>면적(㎡)</th>",
        "<th style='width:10%;'>층수</th>",
        "<th style='width:16%;'>매매가</th>",
        "<th style='width:13%;'>계약일</th>",
        "</tr>",
        "</thead>",

        "<tbody>"
    ]

    for idx, row in df.reset_index(
        drop=True
    ).iterrows():

        p_val = format_korean_price(
            row["매매가(만원)"]
        )

        a_val = (
            f"{float(row['면적(㎡)']):.1f}"
            if pd.notnull(
                row["면적(㎡)"]
            )
            else "0.0"
        )

        html_lines.append(
            "<tr>"
            f"<td>{idx + 1}</td>"
            f"<td>{row['물건명']}</td>"
            f"<td>{row['읍면동']}</td>"
            f"<td>{a_val}</td>"
            f"<td>{row['층수']}</td>"
            f"<td>{p_val}</td>"
            f"<td>{row['계약일']}</td>"
            "</tr>"
        )

    html_lines.extend([
        "</tbody>",
        "</table>",
        "</div>"
    ])

    st.markdown(
        "\n".join(html_lines),
        unsafe_allow_html=True
    )


# =============================================================================
# 18. 카카오 지도
# =============================================================================

MAP_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "kakao_map_comp"
    )
)

os.makedirs(
    MAP_DIR,
    exist_ok=True
)

INDEX_HTML_PATH = os.path.join(
    MAP_DIR,
    "index.html"
)

INDEX_HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">

<script
    type="text/javascript"
    src="https://dapi.kakao.com/v2/maps/sdk.js?appkey=__KAKAO_JS_KEY__&libraries=clusterer">
</script>

<style>

html,
body {
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 0;
    background: transparent;
    overflow: hidden;
}

#map {
    width: 100%;
    height: 600px;
    border-radius: 10px;
}

</style>
</head>

<body>

<div id="map"></div>

<script>

var map;
var clusterer;
var markers = [];
var overlays = [];

function sendToStreamlit(type, data) {

    var msg = Object.assign(
        {
            isStreamlitMessage: true,
            type: type
        },
        data
    );

    window.parent.postMessage(
        msg,
        "*"
    );
}


function selectApt(aptName) {

    sendToStreamlit(
        "streamlit:setComponentValue",
        {
            value: {
                apt_name: aptName,
                ts: Date.now()
            }
        }
    );
}


window.addEventListener(
    "message",
    function(event) {

        if (
            event.data &&
            event.data.type ===
            "streamlit:render"
        ) {

            renderMap(
                event.data.args
            );
        }
    }
);


sendToStreamlit(
    "streamlit:componentReady",
    {
        apiVersion: 1
    }
);


sendToStreamlit(
    "streamlit:setFrameHeight",
    {
        height: 655
    }
);


function renderMap(props) {

    var centerLat =
        props.center_lat;

    var centerLng =
        props.center_lng;

    var aptSummary =
        props.apt_summary;


    kakao.maps.load(
        function() {

            var container =
                document.getElementById(
                    "map"
                );


            if (!map) {

                map =
                    new kakao.maps.Map(
                        container,
                        {
                            center:
                                new kakao.maps.LatLng(
                                    centerLat,
                                    centerLng
                                ),
                            level: 5
                        }
                    );

            } else {

                map.setCenter(
                    new kakao.maps.LatLng(
                        centerLat,
                        centerLng
                    )
                );
            }


            if (clusterer) {

                clusterer.clear();
            }


            markers.forEach(
                function(marker) {

                    marker.setMap(null);
                }
            );

            markers = [];


            overlays.forEach(
                function(overlay) {

                    overlay.setMap(null);
                }
            );

            overlays = [];


            clusterer =
                new kakao.maps.MarkerClusterer(
                    {
                        map: map,
                        averageCenter: true,
                        minLevel: 5
                    }
                );


            if (
                aptSummary &&
                aptSummary.length > 0
            ) {

                aptSummary.forEach(
                    function(item) {

                        var pos =
                            new kakao.maps.LatLng(
                                item.lat,
                                item.lng
                            );


                        var marker =
                            new kakao.maps.Marker(
                                {
                                    position: pos,
                                    clickable: true
                                }
                            );


                        kakao.maps.event.addListener(
                            marker,
                            "click",
                            function() {

                                selectApt(
                                    item.apt_name
                                );
                            }
                        );


                        var div =
                            document.createElement(
                                "div"
                            );


                        div.style.cssText =
                            "cursor:pointer;" +
                            "padding:6px 10px;" +
                            "background:white;" +
                            "color:#2c3e50;" +
                            "border:2px solid #e74c3c;" +
                            "border-radius:10px;" +
                            "font-weight:bold;" +
                            "font-size:12px;" +
                            "box-shadow:0 2px 6px rgba(0,0,0,0.25);" +
                            "text-align:center;" +
                            "user-select:none;";


                        div.innerHTML =
                            item.apt_name +
                            "<br>" +
                            "<span style='color:#e74c3c;font-size:13px;'>" +
                            "평균 " +
                            item.avg_price_fmt +
                            "</span> " +
                            "<span style='font-size:11px;color:#7f8c8d;'>" +
                            "(" +
                            item.count +
                            "건)" +
                            "</span>";


                        div.addEventListener(
                            "click",
                            function(e) {

                                e.stopPropagation();

                                selectApt(
                                    item.apt_name
                                );
                            }
                        );


                        var overlay =
                            new kakao.maps.CustomOverlay(
                                {
                                    position: pos,
                                    clickable: true,
                                    content: div,
                                    yAnchor: 2.2
                                }
                            );


                        overlay.setMap(
                            map
                        );


                        markers.push(
                            marker
                        );

                        overlays.push(
                            overlay
                        );
                    }
                );


                clusterer.addMarkers(
                    markers
                );
            }
        }
    );
}

</script>

</body>
</html>
""".replace(
    "__KAKAO_JS_KEY__",
    KAKAO_JS_KEY
)


with open(
    INDEX_HTML_PATH,
    "w",
    encoding="utf-8"
) as f:

    f.write(
        INDEX_HTML_CONTENT
    )


kakao_map_component = (
    components.declare_component(
        "kakao_map_comp",
        path=MAP_DIR
    )
)


# =============================================================================
# 19. 세션 상태
# =============================================================================

def reset_filter_callback():

    st.session_state[
        "select_apt_dropdown"
    ] = "전체 보기"


if "select_apt_dropdown" not in st.session_state:

    st.session_state[
        "select_apt_dropdown"
    ] = "전체 보기"


if "last_click_ts" not in st.session_state:

    st.session_state[
        "last_click_ts"
    ] = None


# =============================================================================
# 20. 사이드바
# =============================================================================

st.sidebar.markdown(
    """
    <h3 style="
        font-size:20px;
        font-weight:bold;
        margin-bottom:0px;
    ">
        🏢 한국자산관리아카데미
    </h3>
    """,
    unsafe_allow_html=True
)

st.sidebar.title(
    "📍 주소 및 조건"
)


with st.sidebar.form(
    key="search_form"
):

    st.markdown(
        """
        <p style="
            font-weight:bold;
            font-size:16px;
            margin-bottom:6px;
        ">
            주소 및 건물명
        </p>
        """,
        unsafe_allow_html=True
    )


    search_query_input = st.text_input(
        "검색할 주소 또는 건물명/도로명",
        value="호수로 688",
        label_visibility="collapsed"
    )


    st.markdown(
        "<div style='margin-top:12px;'></div>",
        unsafe_allow_html=True
    )


    st.markdown(
        """
        <p style="
            font-weight:bold;
            font-size:16px;
            margin-bottom:6px;
        ">
            부동산 유형
        </p>
        """,
        unsafe_allow_html=True
    )


    property_type_input = st.selectbox(
        "부동산 유형 선택",
        options=[
            "아파트",
            "연립/다세대",
            "단독/다가구",
            "오피스텔",
            "토지"
        ],
        index=0,
        label_visibility="collapsed"
    )


    st.markdown(
        "<div style='margin-top:16px;'></div>",
        unsafe_allow_html=True
    )


    st.markdown(
        f"""
        <p style="
            font-size:13px;
            color:#6b7280;
            font-weight:500;
        ">
            ℹ️ 시군구 단위 국토부 API에서
            최근 {RECENT_MONTHS}개월 데이터를 수집한 후,
            검색 주소 반경 {SEARCH_RADIUS_KM}km 내
            읍·면·동만 필터링합니다.
        </p>
        """,
        unsafe_allow_html=True
    )


    st.markdown(
        "<div style='margin-top:10px;'></div>",
        unsafe_allow_html=True
    )


    search_button = st.form_submit_button(
        "🔍 위치 검색",
        use_container_width=True
    )


# =============================================================================
# 21. 초기 검색
# =============================================================================

if "candidates" not in st.session_state:

    initial_candidates = (
        search_location_candidates(
            "호수로 688"
        )
    )

    st.session_state[
        "candidates"
    ] = initial_candidates

    st.session_state[
        "selected_candidate_idx"
    ] = 0

    st.session_state[
        "submitted_property_type"
    ] = "아파트"


# =============================================================================
# 22. 검색 버튼
# =============================================================================

if search_button:

    new_candidates = (
        search_location_candidates(
            search_query_input
        )
    )

    st.session_state[
        "candidates"
    ] = new_candidates

    st.session_state[
        "selected_candidate_idx"
    ] = 0

    st.session_state[
        "select_apt_dropdown"
    ] = "전체 보기"

    st.session_state[
        "last_click_ts"
    ] = None

    st.session_state[
        "submitted_property_type"
    ] = property_type_input


# =============================================================================
# 23. 검색 후보
# =============================================================================

candidates = st.session_state.get(
    "candidates",
    []
)

selected_candidate = None


if candidates:

    cand_options = [
        f"{i + 1}. {c['display_name']}"
        for i, c in enumerate(candidates)
    ]

    current_idx = st.session_state.get(
        "selected_candidate_idx",
        0
    )

    if current_idx >= len(
        cand_options
    ):

        current_idx = 0


    st.sidebar.markdown("---")


    selected_cand_str = (
        st.sidebar.selectbox(
            "📍 검색된 장소 목록",
            options=cand_options,
            index=current_idx,
            key="candidate_selectbox_widget"
        )
    )


    selected_idx = (
        cand_options.index(
            selected_cand_str
        )
    )


    st.session_state[
        "selected_candidate_idx"
    ] = selected_idx


    selected_candidate = (
        candidates[selected_idx]
    )

else:

    st.sidebar.warning(
        "⚠️ 검색된 위치가 없습니다. "
        "다른 검색어를 입력해 보세요."
    )


# =============================================================================
# 24. 메인
# =============================================================================

if selected_candidate:

    prop_type = st.session_state.get(
        "submitted_property_type",
        "아파트"
    )


    display_address_str = (
        selected_candidate["address"]
        if selected_candidate["address"]
        else selected_candidate["place_name"]
    )


    spinner_message = (
        f"⏳ [{display_address_str}] "
        f"반경 {SEARCH_RADIUS_KM}km 내 "
        f"[{prop_type}] 최근 {RECENT_MONTHS}개월 "
        f"거래 정보를 확인하는 중입니다..."
    )


    with st.spinner(
        spinner_message
    ):

        (
            lat,
            lng,
            full_address,
            lawd_cd,
            region_name,
            filtered_df
        ) = fetch_real_estate_for_candidate(

            selected_candidate["lat"],
            selected_candidate["lng"],

            selected_candidate["address"],

            selected_candidate["place_name"],

            prop_type
        )


    # -------------------------------------------------------------------------
    # 물건 목록
    # -------------------------------------------------------------------------

    apt_options = [
        "전체 보기"
    ]


    if not filtered_df.empty:

        apt_options += sorted(
            list(
                filtered_df[
                    "물건명"
                ].unique()
            )
        )


    if (
        st.session_state[
            "select_apt_dropdown"
        ]
        not in apt_options
    ):

        st.session_state[
            "select_apt_dropdown"
        ] = "전체 보기"


    # -------------------------------------------------------------------------
    # 안내 배너
    # -------------------------------------------------------------------------

    emd_count = (
        filtered_df["읍면동"].nunique()
        if not filtered_df.empty
        else 0
    )


    st.markdown(
        f"""
        <div class="info-banner">

            📍 <b>선택 위치:</b>
            {full_address}

            <br>

            📏 <b>검색 반경:</b>
            {SEARCH_RADIUS_KM}km

            <br>

            🏘️ <b>수집 읍·면·동:</b>
            {emd_count}개

            <br>

            📅 <b>조회 기간:</b>
            최근 {RECENT_MONTHS}개월

            <br>

            📊 <b>실거래:</b>
            총 <b>{len(filtered_df):,}건</b>

        </div>
        """,
        unsafe_allow_html=True
    )


    # -------------------------------------------------------------------------
    # 지도 + 상세
    # -------------------------------------------------------------------------

    col_map, col_detail = st.columns(
        [1, 1],
        gap="medium"
    )


    # =========================================================================
    # 지도
    # =========================================================================

    with col_map:

        st.subheader(
            "🗺️ 부동산 실거래 지도"
        )


        map_center_lat = lat
        map_center_lng = lng


        current_selected = (
            st.session_state[
                "select_apt_dropdown"
            ]
        )


        if (
            current_selected != "전체 보기"
            and not filtered_df.empty
        ):

            match_row = (
                filtered_df[
                    filtered_df["물건명"]
                    == current_selected
                ]
            )


            if not match_row.empty:

                map_center_lat = (
                    match_row[
                        "lat"
                    ].iloc[0]
                )

                map_center_lng = (
                    match_row[
                        "lng"
                    ].iloc[0]
                )


        # ---------------------------------------------------------------------
        # 건물별 집계
        # ---------------------------------------------------------------------

        apt_summary_list = []


        if not filtered_df.empty:

            apt_grp = (
                filtered_df
                .groupby("물건명")
                .agg(
                    평균매매가=(
                        "매매가(만원)",
                        "mean"
                    ),

                    거래건수=(
                        "매매가(만원)",
                        "count"
                    ),

                    lat=(
                        "lat",
                        "first"
                    ),

                    lng=(
                        "lng",
                        "first"
                    )
                )
                .reset_index()
            )


            for _, row in apt_grp.iterrows():

                apt_summary_list.append({

                    "apt_name":
                        row["물건명"],

                    "avg_price_fmt":
                        format_korean_price(
                            row["평균매매가"]
                        ),

                    "count":
                        int(
                            row["거래건수"]
                        ),

                    "lat":
                        float(
                            row["lat"]
                        ),

                    "lng":
                        float(
                            row["lng"]
                        )
                })


        clicked_data = (
            kakao_map_component(
                key="kakao_map_comp",

                center_lat=
                    map_center_lat,

                center_lng=
                    map_center_lng,

                apt_summary=
                    apt_summary_list
            )
        )


        if isinstance(
            clicked_data,
            dict
        ):

            clicked_apt = (
                clicked_data.get(
                    "apt_name"
                )
            )

            click_ts = (
                clicked_data.get(
                    "ts"
                )
            )


            if (
                click_ts
                and click_ts
                != st.session_state.get(
                    "last_click_ts"
                )
            ):

                st.session_state[
                    "last_click_ts"
                ] = click_ts

                st.session_state[
                    "select_apt_dropdown"
                ] = clicked_apt

                st.rerun()


    # =========================================================================
    # 상세
    # =========================================================================

    with col_detail:

        st.subheader(
            "📊 물건 상세 내용"
        )


        if not filtered_df.empty:

            st.markdown(
                """
                <p style="
                    font-weight:bold;
                    margin-bottom:5px;
                    font-size:15px;
                ">
                    🔍 상세 검색 필터
                </p>
                """,
                unsafe_allow_html=True
            )


            col_sel, col_btn = st.columns(
                [4, 1]
            )


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
                    on_click=
                        reset_filter_callback
                )


            active_apt = (
                st.session_state[
                    "select_apt_dropdown"
                ]
            )


            if (
                active_apt
                != "전체 보기"
            ):

                display_df = (
                    filtered_df[
                        filtered_df["물건명"]
                        == active_apt
                    ].copy()
                )


                avg_price_str = (
                    format_korean_price(
                        display_df[
                            "매매가(만원)"
                        ].mean()
                    )
                    if not display_df.empty
                    else "0만"
                )


                st.info(
                    f"🏢 **{active_apt}** "
                    f"({len(display_df)}건) | "
                    f"💰 **평균 매매가:** "
                    f"{avg_price_str}"
                )


            else:

                display_df = (
                    filtered_df.copy()
                )


                st.caption(
                    "💡 지도에서 물건을 클릭하면 "
                    "해당 물건만 필터링됩니다."
                )


            render_custom_centered_table(
                display_df
            )


        else:

            st.info(
                f"""
                해당 검색 위치 반경
                {SEARCH_RADIUS_KM}km 내
                최근 {RECENT_MONTHS}개월
                [{prop_type}] 실거래가 데이터가 없습니다.

                ※ 국토교통부 API에서 제공되지 않는
                거래이거나 해당 지역에 거래가 없을 수 있습니다.
                """
            )
