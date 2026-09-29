# -*- coding: utf-8 -*-
"""
cloud_probe/app.py - 클라우드(Streamlit Community Cloud 등)에서 데이터 수집이 되는지 확인하는 독립 페이지.

StockAI 본체 모듈을 import 하지 않는다. 버튼 "수집 테스트"를 누르면 5개 수집을 각각 try/except로 실행하고
성공/실패, 행 수, 에러 메시지를 표로 보여 준다.
키는 st.secrets 에서만 읽는다(FSS_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET). 키 값은 화면·로그에 쓰지 않으며,
에러 메시지에 키가 섞여 들어오면(예: 요청 URL) *** 로 가린다.
"""

import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

SECRET_NAMES = ("FSS_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
SAMSUNG_CORP_CODE = "00126380"          # DART 고유번호(삼성전자)
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
TIMEOUT = 20


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


PROBES = [
    ("1. FDR StockListing('KRX')", probe_fdr_listing),
    ("2. FDR DataReader('KS11') 최근 30일", probe_fdr_index),
    ("3. pykrx 005930 OHLCV 최근 30일", probe_pykrx_ohlcv),
    ("4. DART company.json 삼성전자", probe_dart_company),
    ("5. 네이버 뉴스 API '삼성전자' 1건", probe_naver_news),
]


def run_probes():
    rows = []
    for label, func in PROBES:
        t0 = time.perf_counter()
        try:
            count, note = func()
            rows.append({"항목": label, "결과": "성공", "행 수": count, "메모/에러": _redact(note)})
        except Exception as exc:
            rows.append({"항목": label, "결과": "실패", "행 수": 0,
                         "메모/에러": _redact(f"{type(exc).__name__}: {exc}")[:300]})
        rows[-1]["초"] = round(time.perf_counter() - t0, 1)
    return pd.DataFrame(rows)


def main():
    st.set_page_config(page_title="StockAI cloud probe", page_icon="🛰️")
    st.title("StockAI 클라우드 수집 점검")
    st.caption("키 준비 여부: " + " · ".join(f"{n} {'있음' if _secret(n) else '없음'}" for n in SECRET_NAMES))
    if st.button("수집 테스트", type="primary"):
        with st.spinner("5개 수집을 실행하는 중..."):
            table = run_probes()
        st.dataframe(table, hide_index=True, width="stretch")


if __name__ == "__main__":
    main()
