# curael-pdp-dashboard

큐라엘몰(curaelmall.com) 상품 상세페이지 이탈 분석 파이프라인.

GA4·Clarity·카페24 세 소스를 **상품번호로 묶어** 매일 적재하고,
"상세페이지 몇 번째 이미지에서 사람들이 나가는가 → 그게 돈으로 얼마인가"를 한 화면에서 본다.

---

## ⚠️ 가장 중요한 규칙

> ### Clarity 데이터는 최근 3일치만 받을 수 있다.
> **매일 받지 않으면 그 기간은 영구 소실된다. 복구 방법이 없다.**
> `fetch_clarity_pdp.py`가 하루라도 안 돌면 그날은 끝이다.

그래서 이 저장소는 적재를 최우선으로 만들었다. 화면은 나중에 채워도 된다.

---

## 현재 상태

| 구성요소 | 상태 |
|---|---|
| `pdp_common.py` — 공용 유틸·상품번호 파싱 | ✅ 완료 (10케이스 검증) |
| `detect_version.py` — 버전 자동 감지 | ✅ 완료 (v1.0 기준선 확정, 오탐 0건 확인) |
| `clarity_api.py` / `fetch_clarity_pdp.py` | ⏸ 코드 완료 · **`CLARITY_API_TOKEN` 필요** |
| `fetch_ga4_pdp.py` | ⬜ 미착수 · GA4 서비스계정 권한 필요 |
| `fetch_cafe24_pdp.py` | ⬜ 미착수 |
| `merge_pdp.py` / `build_pdp_dashboard.py` | ⬜ 2단계 (데이터 3~7일 후) |

---

## 사용법

```bash
# 버전 감지 (인증 불필요, 매일 실행)
python detect_version.py
python detect_version.py --dry-run      # 저장 없이 확인

# Clarity 수집 — 반드시 KST 00:20 에
python fetch_clarity_pdp.py
python fetch_clarity_pdp.py --dump-raw  # 첫 실행 시 필수: URL 원본값 확인
python fetch_clarity_pdp.py --repair --days 3   # 공백 보충(합산값)
```

### 첫 Clarity 실행 시 반드시 `--dump-raw` 로 확인할 것

**최대 리스크**: Clarity의 `URL` 차원이 쿼리스트링을 유지하는지 모른다.
제거된다면 `/product/detail.html?product_no=26`과 `...=76`이 **한 줄로 뭉개져**
상품 귀속이 불가능해진다.

그 경우 → 슬러그형 URL만 상품별로 쓰고 나머지는 `_unknown`으로 **분리 보고**한다.
GA4 비율로 안분하지 않는다. 그건 측정이 아니라 추정이다.

---

## 설계상 반드시 지킬 것

**1. Clarity는 append-only, GA4는 덮어쓰기.**
Clarity는 3일 창을 벗어나면 영구 소실이라 절대 덮어쓰지 않는다(`--force` 제외).
GA4는 약 48시간 확정되지 않으므로 매 실행마다 D-1·D-2·D-3을 다시 받아 덮어쓴다.

**2. Clarity 호출은 하루 10회.** 자동 4건만 쓰고 **3건은 사람 몫으로 남긴다**(`RESERVE`).
자동화가 다 태우면 사람이 급할 때 못 쓴다.

**3. 3차원 조합(`URL×Device×Source`)은 쓰지 않는다.**
행수가 곱으로 늘어 1,000행 상한에 걸리는데 **API가 잘렸다고 알려주지 않는다.**
조용히 틀린 데이터가 가장 나쁘다. `Device` 단독 호출을 기준값으로 두고 잘림을 감시한다.

**4. `merge_pdp.py`는 네트워크를 호출하지 않는다.**
3소스가 서로 다른 시각·주체(로컬/클라우드)로 도착하므로, 병합은 몇 번 다시 돌려도
같은 결과여야 한다. 수집기 하나가 죽어도 나머지로 대시보드가 나온다.

**5. "0"과 "미수집"을 구분한다.** 카페24는 로컬 PC 의존이라 결손이 잦다.
둘을 구분하지 못하면 대시보드가 거짓말을 한다.

---

## 버전 자동 감지 방식

상세 이미지 URL 목록의 해시로 판정한다. GA4의 `pdp_section_total`은 세션마다
흔들려서(레이지 이미지 로드 실패) 판정 근거로 못 쓴다.

**오탐 완화 4단**
1. **정규화** — 쿼리·CDN 경로·카페24 복제 접두어(`copy-<epoch>-`) 제거.
   이걸 안 벗기면 같은 이미지를 재업로드할 때마다 오탐
2. **공용 배너 제외** — ⓐ 전체 상품 50% 이상에 등장, 또는 ⓑ 2개 이상 상품에서
   **항상 맨 앞**. 실측상 `notice_banner`가 주력 2개 상품에만 걸려 있어
   ⓐ만으로는 못 걸러진다. 위치 신호가 이름 휴리스틱보다 정확하다
3. **변경 등급** — major(개수 변경 / 20%↑ 교체 / **앞 3장** 교체) · minor · none.
   앞 3장을 특별 취급하는 이유: 이탈 대부분이 초반에서 나므로 후킹 이미지 1장 교체는
   "1/20 = 5%"여도 영향이 후반 5장 교체보다 크다
4. **디바운스** — 연속 2회 관측되어야 확정. **0장 응답은 수집 실패로 폐기**
   (실제로 상품 28번이 일시적으로 0장으로 잡히는 것을 관측했다)

수동 보정: `data/pdp_version_overrides.json`
```json
{ "26": [{"date":"2026-08-20","version":2,"note":"상세 전면 개편"}] }
```

---

## 필요한 자격증명

| 이름 | 위치 | 비고 |
|---|---|---|
| `CLARITY_API_TOKEN` | GitHub Secret + 로컬 | Clarity → 설정 → 데이터 내보내기 → 새 API 토큰 생성 |
| `GOOGLE_SA_JSON` | GitHub Secret | vegicel에서 재사용. **GA4 속성에 뷰어로 초대 필요** |
| `GA4_PROPERTY_ID` | GitHub Secret | `G-LY9GWWNFZ1`이 아니라 **숫자 속성 ID** |
| `CAFE24_*` | 로컬 `collect_pdp.bat`만 | 토큰 파일이 로컬 전용이라 Actions에서 못 돈다 |

---

## 연관 자산

- 추적 스크립트 원본: `curael-ai-company-os-main/docs/analytics/pdp-tracking.js`
- Clarity 커스텀 태그: 같은 폴더 `clarity-pdp-tags.js`
- 진단·설치 문서: 같은 폴더 `추적진단_2026-08-06.md`, `설치가이드_상세페이지이탈추적.md`
- 자매 프로젝트(관례의 원본): `C:\Users\dongs\vegicel-ad-autopilot`
