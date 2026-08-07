# 큐라엘몰 UTM 규칙

## 왜 필요한가

2026-08-06 진단 결과, 상세페이지 유입의 **67%가 "미분류"** 였다.
GA4에서 그 세션들을 열어보니 `session_start` 이벤트가 없다 — 세션의 시작을 GA4가
보지 못했다는 뜻이고, 시작을 못 보면 유입 출처를 붙일 자리가 없다.

원인은 **앱 내부 브라우저**다. 유튜브·인스타그램 앱에서 링크를 누르면 앱이 자체
브라우저로 열면서 리퍼러(어디서 왔는지)를 지운다. 그래서 유튜브에서 아무리 많이 와도
GA4에는 "출처 없음"으로 찍힌다.

**리퍼러는 지워져도 URL에 직접 적은 값은 안 지워진다.** 그게 UTM 파라미터다.

---

## 규칙

기본 주소 뒤에 `?` 를 붙이고 아래 3개를 적는다. (이미 `?` 가 있으면 `&` 로 잇는다)

| 파라미터 | 뜻 | 값 |
|---|---|---|
| `utm_source` | 어디서 | `youtube` `instagram` `facebook` `kakao` `naver` |
| `utm_medium` | 어떤 형태 | `video`(영상 설명란) `paid`(광고) `social`(게시물) `message`(알림톡) |
| `utm_campaign` | 무엇 | 영상·광고를 구분할 이름 (영문·숫자·`_` 만) |

`utm_campaign` 은 **나중에 자기가 알아볼 수 있는 이름**이면 된다. 날짜를 앞에 붙이면
정렬이 편하다: `2608_vegicel_review`

---

## 바로 쓰는 예시

**유튜브 영상 설명란 — 베지셀 상세로**
```
https://curaelmall.com/product/detail.html?product_no=26&utm_source=youtube&utm_medium=video&utm_campaign=2608_vegicel
```

**유튜브 고정댓글 — 전체상품으로**
```
https://curaelmall.com/category/전체상품/54/?utm_source=youtube&utm_medium=video&utm_campaign=2608_pinned
```

**인스타 프로필 링크**
```
https://curaelmall.com/?utm_source=instagram&utm_medium=social&utm_campaign=bio_link
```

**카카오 알림톡**
```
https://curaelmall.com/product/detail.html?product_no=26&utm_source=kakao&utm_medium=message&utm_campaign=2608_restock
```

---

## 손대지 않아도 되는 것

| 채널 | 이유 |
|---|---|
| **구글 애즈** | 자동 태그(`gclid`)가 붙는다. UTM 을 수동으로 붙이면 **오히려 자동 태그를 덮어써** 전환 추적이 깨진다. **절대 붙이지 말 것.** |
| **메타(인스타·페이스북) 광고** | 광고 관리자에 URL 매개변수 칸이 있다. 거기 넣으면 전 광고에 자동 적용된다. 광고 링크를 손으로 고치지 말 것. |
| **유튜브 쇼핑 선반** | `youtube / product_shelf` 로 이미 정상 분류된다. |

즉 **손으로 UTM 을 붙일 곳은 영상 설명란·고정댓글·프로필 링크·알림톡** 처럼
"내가 직접 주소를 적는 자리" 뿐이다.

---

## 확인 방법

링크를 바꾸고 하루 뒤, 대시보드의 **「어디서 들어왔나」** 표에서 미분류 비중이
내려갔는지 본다. 30% 아래로 내려가면 채널별 판단이 가능해진다.

GA4 에서 바로 보려면: 보고서 → 획득 → 트래픽 획득 → 세션 소스/매체.

---

## 주의

- 파라미터는 **소문자**로 통일한다. GA4 는 `YouTube` 와 `youtube` 를 다른 값으로 센다.
- 한글·공백을 쓰지 않는다. 앱마다 인코딩이 달라 값이 깨진다.
- 같은 캠페인은 **같은 철자**를 쓴다. `2608_vegicel` 과 `2608-vegicel` 은 따로 집계된다.
