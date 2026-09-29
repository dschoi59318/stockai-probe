# -*- coding: utf-8 -*-
"""
cloud_probe/app.py - 클라우드(Streamlit Community Cloud 등)에서 데이터 수집이 되는지 확인하는 독립 페이지.

StockAI 본체 모듈을 import 하지 않는다. 버튼 "수집 테스트"를 누르면 7개 수집(v2: 6 corpCode.xml, 7 fnlttSinglAcnt 추가)을 각각 try/except로 실행하고
성공/실패, 행 수, 에러 메시지를 표로 보여 준다.
키는 st.secrets 에서만 읽는다(FSS_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET). 키 값은 화면·로그에 쓰지 않으며,
에러 메시지에 키가 섞여 들어오면(예: 요청 URL) *** 로 가린다.
"""

import io
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

SECRET_NAMES = ("FSS_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
DART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
SAMSUNG_CORP_CODE = "00126380"          # DART 고유번호(삼성전자)
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
TIMEOUT = 20
CORPCODE_TIMEOUT = 60

_state = {}                             # 한 번의 테스트 안에서 6번 결과(corp_code)를 7번에 넘긴다


def _secret(name):
    """st.secrets 값(없으면 빈 문자열). secrets.toml 자체가 없어도 예외 없이 빈 문자열."""
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


def _redact(text):
    """문자열 안의 키 값을 *** 로 바꾼다."""
    text = str(text)
    for name in SECRET_NAMES:
        value = _secret(name)
        if value:
            text = text.replace(value, "***")
    return text


def _range(days):
    end = datetime.now()
    return end - timedelta(days=days), end


def probe_fdr_listing():
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    return len(df), f"열 {len(df.columns)}개"


def probe_fdr_index():
    import FinanceDataReader as fdr
    start, end = _range(30)
    df = fdr.DataReader("KS11", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    last = f"마지막 {df.index[-1]:%Y-%m-%d}" if len(df) else ""
    return len(df), last


def probe_pykrx_ohlcv():
    from pykrx import stock
    start, end = _range(30)
    df = stock.get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930")
    last = f"마지막 {df.index[-1]:%Y-%m-%d}" if len(df) else ""
    return len(df), last


def probe_dart_company():
    key = _secret("FSS_KEY")
    if not key:
        raise RuntimeError("st.secrets['FSS_KEY'] 가 비어 있음")
    r = requests.get(DART_COMPANY_URL, params={"crtfc_key": key, "corp_code": SAMSUNG_CORP_CODE}, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "000":
        raise RuntimeError(f"DART status {body.get('status')}: {body.get('message')}")
    return 1, body.get("corp_name", "")


def probe_naver_news():
    cid, secret = _secret("NAVER_CLIENT_ID"), _secret("NAVER_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError("st.secrets['NAVER_CLIENT_ID'] / ['NAVER_CLIENT_SECRET'] 가 비어 있음")
    r = requests.get(NAVER_NEWS_URL, params={"query": "삼성전자", "display": 1},
                     headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("items", [])
    return len(items), f"전체 {r.json().get('total', 0):,}건"


def probe_dart_corpcode():
    """corpCode.xml(zip) 다운로드: HTTP 상태·Content-Type·바이트·회사 수·삼성전자 corp_code. zip이 아니면 본문 앞 200자."""
    key = _secret("FSS_KEY")
    if not key:
        raise RuntimeError("st.secrets['FSS_KEY'] 가 비어 있음")
    t0 = time.perf_counter()
    r = requests.get(DART_CORPCODE_URL, params={"crtfc_key": key}, timeout=CORPCODE_TIMEOUT)
    head = (f"HTTP {r.status_code} · Content-Type {r.headers.get('Content-Type', '-')} · "
            f"{len(r.content):,}바이트 · {time.perf_counter() - t0:.1f}초")
    if not zipfile.is_zipfile(io.BytesIO(r.content)):
        raise RuntimeError(f"{head} · zip 아님 · 본문 앞 200자: {r.content[:200].decode('utf-8', 'replace')}")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    companies = root.findall("list")
    samsung = next((c.findtext("corp_code") for c in companies if (c.findtext("stock_code") or "").strip() == "005930"),
                   None)
    _state["corp_code"] = samsung
    return len(companies), f"{head} · 회사 {len(companies):,}개 · 005930 corp_code {samsung or '못 찾음'}"


def probe_dart_fnltt():
    """fnlttSinglAcnt 삼성전자 2026 반기(11012). corp_code 는 6번 결과, 없으면 00126380."""
    key = _secret("FSS_KEY")
    if not key:
        raise RuntimeError("st.secrets['FSS_KEY'] 가 비어 있음")
    corp = _state.get("corp_code") or SAMSUNG_CORP_CODE
    source = "6번 결과" if _state.get("corp_code") else "기본값"
    r = requests.get(DART_FNLTT_URL, params={"crtfc_key": key, "corp_code": corp, "bsns_year": "2026",
                                             "reprt_code": "11012"}, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    status, message, rows = body.get("status"), body.get("message"), len(body.get("list") or [])
    note = f"DART status {status} · {message} · 행 {rows}개 · corp_code {corp}({source})"
    if status != "000":
        raise RuntimeError(note)
    return rows, note


PROBES = [
    ("1. FDR StockListing('KRX')", probe_fdr_listing),
    ("2. FDR DataReader('KS11') 최근 30일", probe_fdr_index),
    ("3. pykrx 005930 OHLCV 최근 30일", probe_pykrx_ohlcv),
    ("4. DART company.json 삼성전자", probe_dart_company),
    ("5. 네이버 뉴스 API '삼성전자' 1건", probe_naver_news),
    ("6. DART corpCode.xml 다운로드", probe_dart_corpcode),
    ("7. DART fnlttSinglAcnt 삼성전자 2026 반기", probe_dart_fnltt),
]


def run_probes():
    _state.clear()
    rows = []
    for label, func in PROBES:
        t0 = time.perf_counter()
        try:
            count, note = func()
            rows.append({"항목": label, "결과": "성공", "행 수": count, "메모/에러": _redact(note)})
        except Exception as exc:
            rows.append({"항목": label, "결과": "실패", "행 수": 0,
                         "메모/에러": _redact(f"{type(exc).__name__}: {exc}")[:500]})
        rows[-1]["초"] = round(time.perf_counter() - t0, 1)
    return pd.DataFrame(rows)


def main():
    st.set_page_config(page_title="StockAI cloud probe", page_icon="🛰️")
    st.title("StockAI 클라우드 수집 점검")
    st.caption("키 준비 여부: " + " · ".join(f"{n} {'있음' if _secret(n) else '없음'}" for n in SECRET_NAMES))
    if st.button("수집 테스트", type="primary"):
        with st.spinner(f"{len(PROBES)}개 수집을 실행하는 중..."):
            table = run_probes()
        st.dataframe(table, hide_index=True, width="stretch")


if __name__ == "__main__":
    main()
