"""GA4 Data API v1beta 클라이언트.

인증은 vegicel-ad-autopilot/export_sheets.py:27-43 의 서비스계정 패턴을 그대로 쓰고
scope만 analytics.readonly 로 바꾼다. GOOGLE_SA_JSON 시크릿이 이미 존재한다.

사전 준비:
  1) GA4 속성에 서비스계정을 뷰어로 초대  (없으면 전부 403)
  2) Google Cloud 프로젝트에서 Google Analytics Data API 사용 설정
"""
import json
import os
import urllib.error
import urllib.request

DATA_API = "https://analyticsdata.googleapis.com/v1beta"
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


class GA4Error(RuntimeError):
    pass


def _sa_info():
    """GOOGLE_SA_JSON(전문) → GOOGLE_SA_FILE(경로) → .env 순으로 찾는다."""
    raw = os.getenv("GOOGLE_SA_JSON", "").strip()
    if raw:
        return json.loads(raw)

    path = os.getenv("GOOGLE_SA_FILE", "").strip()
    if not path:
        try:
            with open(".env", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GOOGLE_SA_FILE="):
                        path = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
    if not path:
        raise GA4Error(
            "GOOGLE_SA_JSON 또는 GOOGLE_SA_FILE 이 없습니다. .env 에 경로를 적으세요.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def token():
    """서비스계정 JWT → access token. google-auth 필요."""
    try:
        import google.auth.transport.requests
        from google.oauth2 import service_account
    except ImportError as e:
        raise GA4Error("google-auth 가 필요합니다:  pip install google-auth") from e

    creds = service_account.Credentials.from_service_account_info(
        _sa_info(), scopes=[SCOPE])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def property_id():
    p = os.getenv("GA4_PROPERTY_ID", "").strip()
    if not p:
        try:
            with open(".env", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GA4_PROPERTY_ID="):
                        p = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
    if not p:
        raise GA4Error("GA4_PROPERTY_ID 가 없습니다. (숫자 속성 ID, G- 아님)")
    return p


def run_report(dimensions, metrics, start, end, event_name=None,
               limit=100000, access=None, prop=None):
    """runReport 1건. rowCount 가 더 크면 offset 을 올려 전부 받아온다.

    dimensions/metrics 는 API 이름 문자열 리스트.
      맞춤 측정기준 → "customEvent:pdp_product_id"
      맞춤 측정항목 → "customEvent:pdp_seconds"  (합계로 온다. 평균은 직접 계산)
    """
    access = access or token()
    prop = prop or property_id()
    rows, offset = [], 0

    while True:
        body = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "limit": limit,
            "offset": offset,
            "keepEmptyRows": False,
        }
        if event_name:
            body["dimensionFilter"] = {
                "filter": {"fieldName": "eventName",
                           "stringFilter": {"value": event_name}}}

        req = urllib.request.Request(
            f"{DATA_API}/properties/{prop}:runReport",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {access}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:600]
            except Exception:
                pass
            raise GA4Error(f"HTTP {e.code}: {detail}")

        got = payload.get("rows") or []
        dim_names = [h["name"] for h in payload.get("dimensionHeaders", [])]
        met_names = [h["name"] for h in payload.get("metricHeaders", [])]
        for row in got:
            rec = {}
            for i, v in enumerate(row.get("dimensionValues", [])):
                rec[dim_names[i]] = v.get("value")
            for i, v in enumerate(row.get("metricValues", [])):
                rec[met_names[i]] = v.get("value")
            rows.append(rec)

        total = int(payload.get("rowCount") or 0)
        offset += len(got)
        if not got or offset >= total:
            break

    return rows
