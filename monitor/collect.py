"""침대 브랜드 데일리 모니터 수집기.

뉴스(Google 뉴스 RSS)와 유튜브(공식 채널 RSS + 키워드 검색 '오늘 업로드')에서
최근 글을 모아 기존 기록(items.json)에 새 글만 더한다.
사용: python3 monitor/collect.py monitor/data/items.json [ads_raw.json]
ads_raw.json(선택): Uplika 광고 검색(Meta 광고 라이브러리) 결과를 [{"brand": "씰리", "ads": [...]}] 로 모은 파일.
"""
import json, re, sys, time, urllib.error, urllib.parse, urllib.request, html
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
KEEP_DAYS = 45      # 기록 보관 기간
MONDAY = NOW.weekday() == 0  # 월~금만 돌므로 월요일엔 주말치까지 본다
WINDOW_H = 84 if MONDAY else 36  # 이번 실행에서 '최근'으로 보는 범위(시간)
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"}

# 브랜드, 구분, 뉴스 검색어, 유튜브 검색어, 공식 유튜브 채널 ID(확인된 것만),
# 제목에 꼭 있어야 할 단어(하나 이상), 함께 있어야 할 단어(비면 조건 없음), 빼는 단어
BRANDS = [
    ("시몬스", "자사", "시몬스", ["시몬스침대", "시몬스 매트리스"], "UCHUsIuynbZWCVs7hSwPHxdQ",
     ["시몬스", "simmons"],
     [],
     # 사람 이름 '시몬스'(운동선수·배우·가수)를 거르는 단어. 제목에 하나라도 있으면 뺀다
     ["벤 시몬스", "JK 시몬스", "J.K. 시몬스", "리처드 시몬스", "진 시몬스", "사비 시몬스", "선수", "감독", "부상",
      "아킬레스", "NBA", "축구", "농구", "야구선수", "이적", "리그", "득점", "훈련", "시즌 아웃", "배우",
      "영화", "드라마", "할리우드", "가수", "별세", "사망", "nba", "nfl", "psg", "토트넘", "라이프치히",
      "데뷔전", "맹활약", "연승", "연패", "태클", "쿼터백", "골키퍼", "슈팅", "어시스트", "트레이드"]),
    ("N32", "자사", "N32", ["N32 침대", "N32 매트리스"], "UCcQQvh4I2LQBWPtPk0mUPmw", ["n32"], [], []),
    ("몽클로스", "뷰티", "몽클로스 OR MONCLOS", ["몽클로스", "MONCLOS"], None, ["몽클로스", "monclos"], [], []),
    ("베스트슬립", "경쟁사", "베스트슬립", ["베스트슬립"], "UCOc_C-AP_16htnpXAMl6Kjw", ["베스트슬립", "bestsleep"], [], []),
    ("씰리", "경쟁사", "씰리", ["씰리침대"], "UCCn9ZfSzz2Q7Dg3j3zPuErA", ["씰리", "sealy"], [], []),
    ("템퍼", "경쟁사", "템퍼 OR TEMPUR", ["템퍼 매트리스"], "UCB347iopCDS6yPAWgP1xASQ", ["템퍼", "tempur"],
     ["매트리스", "침대", "베개", "tempur", "수면"], ["템퍼링"]),
    ("에이스", "경쟁사", "에이스침대", ["에이스침대"], "UCSZiIVgGA4JAIJgoAghGGHA", ["에이스침대", "ace bed"], [], []),
    ("코웨이", "경쟁사", "코웨이 OR 비렉스", ["코웨이 매트리스", "비렉스 매트리스"], None,
     ["코웨이", "비렉스", "berex", "coway"], ["비렉스", "berex", "매트리스", "침대", "수면", "슬립", "안마"], []),
]


def match(text, must, also, exclude):
    t = text.lower()
    return (any(w.lower() in t for w in must) and (not also or any(w.lower() in t for w in also))
            and not any(w.lower() in t for w in exclude))


def get(url):
    for wait in (0, 20, 60):  # 요청 제한(429)이 오면 잠시 쉬었다가 다시 시도
        time.sleep(wait or 1.5)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait == 60:
                raise


def news(brand, group, q, rule):
    q += " when:4d" if MONDAY else " when:2d"
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    out = []
    for it in ET.fromstring(get(url)).iter("item"):
        title = it.findtext("title") or ""
        src = it.findtext("source") or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        if NOW - pub > timedelta(hours=WINDOW_H) or not match(title, *rule):
            continue
        out.append(row(brand, group, "뉴스", "언론", title, src, it.findtext("link"), pub))
    return out


def yt_channel(brand, group, cid):
    xml = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(xml)
    chname = root.findtext("a:title", namespaces=ns) or ""
    out = []
    for e in root.findall("a:entry", ns):
        pub = datetime.fromisoformat(e.findtext("a:published", namespaces=ns)).astimezone(KST)
        if NOW - pub > timedelta(hours=WINDOW_H):
            continue
        vid = e.findtext("yt:videoId", namespaces=ns)
        out.append(row(brand, group, "유튜브", "공식", e.findtext("a:title", namespaces=ns), chname,
                       f"https://www.youtube.com/watch?v={vid}", pub))
    return out


REL = re.compile(r"(\d+)\s*(초|분|시간|일|주|개월|년)")
UNIT_H = {"초": 1 / 3600, "분": 1 / 60, "시간": 1, "일": 24, "주": 168, "개월": 720, "년": 8760}


def yt_search(brand, group, query, rule, official):
    # sp=EgQIAhAB : 업로드 '오늘' + 동영상만, EgQIAxAB : '이번 주' (월요일에 주말치 포함)
    sp = "EgQIAxAB" if MONDAY else "EgQIAhAB"
    page = get("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query, "sp": sp}))
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page)
    if not m:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                v = o["videoRenderer"]
                title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                owner = v.get("ownerText", {}).get("runs", [{}])[0]
                cid = owner.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId", "")
                rel = REL.search(v.get("publishedTimeText", {}).get("simpleText", ""))
                # 설명란 태그 도배 영상을 거르려고 제목·채널명에 브랜드가 있을 때만 남긴다
                if rel and match(title + " " + owner.get("text", ""), *rule) and cid != official:
                    pub = NOW - timedelta(hours=int(rel.group(1)) * UNIT_H[rel.group(2)])
                    if NOW - pub > timedelta(hours=WINDOW_H):
                        return
                    out.append(row(brand, group, "유튜브", "언급", title, owner.get("text", ""),
                                   f"https://www.youtube.com/watch?v={v['videoId']}", pub))
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(json.loads(m.group(1)))
    return out


# Meta 광고 라이브러리에서 쓰는 공식 페이지(pageId). 없는 브랜드는 키워드로 찾고 본문에 브랜드가 있어야 남긴다
AD_PAGES = {"시몬스": "142021542805228", "씰리": "557125447477878", "베스트슬립": "2887071251366762",
            "에이스": "250389521725370", "코웨이": "337609789607046"}
AD_KEYWORDS = {"N32": "N32", "템퍼": "템퍼", "몽클로스": "몽클로스"}
AD_ALSO = {"N32": ["매트리스", "침대", "시몬스", "수면", "토퍼"]}  # 키워드 광고에 함께 있어야 할 말
GROUP = {b[0]: b[1] for b in BRANDS}
AD_MARK = re.compile(r"^\s*[#(*]*\s*광고(?![가-힣])\s*\)?\s*(지만)?\s*[•·]?\s*")  # 협찬 광고 앞의 '#광고', '(광고)' 표시


def ads_rows(raw):
    out = []
    for blk in raw:
        brand = blk.get("brand")
        if brand not in GROUP:
            continue
        rule = next((b[5], b[6], b[7]) for b in BRANDS if b[0] == brand)
        page = AD_PAGES.get(brand)
        for ad in blk.get("ads") or []:
            adv = ad.get("advertiser") or {}
            body = (ad.get("title") or "") + "\n" + (ad.get("body") or "")
            if page and adv.get("pageId") != page:
                continue  # 공식 페이지 광고만 (대리점 광고 제외)
            if page and rule[1] and not any(w.lower() in body.lower() for w in rule[1]):
                continue  # 예: 코웨이는 정수기·비데 광고를 빼고 비렉스·매트리스 광고만
            if not page and not match(body + " " + (adv.get("name") or ""), *rule):
                continue
            if not page and brand in AD_ALSO and not any(w.lower() in body.lower() for w in AD_ALSO[brand]):
                continue
            lines = [AD_MARK.sub("", ln).strip(" .-·•\u2800") for ln in body.splitlines()]
            first = next((ln for ln in lines if ln), "")
            try:
                pub = datetime.strptime(ad.get("startDate", ""), "%Y-%m-%d").replace(tzinfo=KST)
            except ValueError:
                pub = NOW
            if NOW - pub > timedelta(days=30):
                continue  # 지금 돌고 있는 광고 중 최근 30일 안에 시작한 것만
            out.append(row(brand, GROUP[brand], "SNS 광고", "공식" if page else "광고", first[:120],
                           adv.get("name") or "", ad.get("libraryUrl") or "", pub))
    return out


def row(brand, group, channel, kind, title, source, url, pub):
    return {"수집일": NOW.strftime("%Y-%m-%d"), "브랜드": brand, "구분": group, "채널": channel,
            "유형": kind, "제목": html.unescape(title or "").strip(), "출처": source or "",
            "게시시각": pub.strftime("%Y-%m-%d %H:%M"), "링크": url}


def main(path, ads_path=None):
    try:
        prev = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        prev = {"rows": []}
    rows = prev["rows"]
    seen = {r["링크"] for r in rows} | {(r["브랜드"], r["제목"]) for r in rows}
    errors, added = [], 0
    for brand, group, nq, yq, cid, must, also, exclude in BRANDS:
        rule = (must, also, exclude)
        jobs = [lambda: news(brand, group, nq, rule)]
        if cid:
            jobs.append(lambda: yt_channel(brand, group, cid))
        jobs += [lambda q=q: yt_search(brand, group, q, rule, cid) for q in yq]
        if ads_path and brand == BRANDS[0][0]:
            jobs.append(lambda: ads_rows(json.load(open(ads_path, encoding="utf-8"))))
        for job in jobs:
            try:
                for r in job():
                    if r["링크"] in seen or (r["브랜드"], r["제목"]) in seen:
                        continue
                    seen |= {r["링크"], (r["브랜드"], r["제목"])}
                    rows.append(r)
                    added += 1
            except Exception as e:  # 한 곳이 실패해도 나머지는 계속
                errors.append(f"{brand}: {type(e).__name__} {e}")
    cutoff = (NOW - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    rows = sorted((r for r in rows if r["수집일"] >= cutoff), key=lambda r: r["게시시각"], reverse=True)
    meta = [
        {"label": "마지막 수집", "value": NOW.strftime("%Y-%m-%d %H:%M KST")},
        {"label": "이번에 새로 추가", "value": str(added)},
        {"label": "수집 실패", "value": "; ".join(errors) or "없음"},
        {"label": "SNS 광고", "value": "Meta 광고 라이브러리 확인함" if ads_path else "이번엔 확인하지 않음"},
        {"label": "collect.py", "value": open(__file__, encoding="utf-8").read(), "type": "code", "lang": "python"},
    ]
    json.dump({"rows": rows, "meta": meta}, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"added={added} total={len(rows)} errors={len(errors)}")
    for e in errors:
        print("ERR", e)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
