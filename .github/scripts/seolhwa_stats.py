#!/usr/bin/env python3
"""설화던전 현황 수집기. GitHub Actions(.github/workflows/seolhwa-stats.yml)가 한 시간마다 돌린다.

  python3 seolhwa_stats.py <출력 폴더>

출력 폴더에 latest.json(지금 값 전부)을 쓰고 history.jsonl(숫자만, 한 줄 = 한 번 수집)에 한 줄 덧붙인다.
hall_rows.json은 전당 기록 요약을 모아 둔 것 — 전당을 통째로 다시 읽지 않으려고 둔다(읽기 한도, hall() 머리말).
페이지(seolhwa-stats/index.html)는 이 둘을 읽어 "달라진 것"을 만든다.
브라우저가 직접 못 읽는 곳(플레이·앱스토어 차트·리뷰·유튜브)은 여기서만 읽는다.

한 곳이 실패해도 나머지는 계속한다 — 실패는 errors에 적고, 그 값은 비운다(페이지가 "못 읽음"으로 보인다).
YT_API_KEY 환경변수가 있으면 유튜브 Data API로, 없으면 채널 RSS(최근 15편) + 채널 탭·영상 페이지(그 전 영상) + 검색 페이지(다른 채널)로 읽는다.
"""
import json, os, re, sys, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

IOS_ID = 6814757012
PLAY_ID = "com.sungkisoft.seolhwadungeon"
YT_HANDLE = "sungkisoft"
FS_DOCS = "https://firestore.googleapis.com/v1/projects/seolhwa-dungeon/databases/(default)/documents"
FS_KEY = "AIzaSyCHvKcIPGZKwk5lwPmvg7AuU1iAhuMheRw"  # 게임에 박혀 나가는 공개 웹 키(hall.gd와 같다). 막는 일은 firestore.rules가 한다
KST = timezone(timedelta(hours=9))
LAUNCH = datetime(2026, 10, 2, tzinfo=KST)  # 전당 "출시 뒤"를 세는 날 (marketing/calendar.md 점검과 같은 기준)
KEEP = 24 * 21  # history.jsonl에 남길 줄 수(약 3주)
# 전당 기록 요약을 모아 두는 파일(데이터 가지). 수집기도 페이지도 이 파일 + "그 뒤에 올라온 것"만 읽는다 — 아래 hall() 머리말
HALL_CACHE = "hall_rows.json"
HALL_COLS = ["id", "at", "uid", "name", "depth", "dungeon", "by", "ver", "species", "cls", "win", "level", "score", "en", "ja"]  # ja: 일본어로 한 판 (2026-10-21). 칸이 바뀌면 hall()이 한 번 통째로 다시 읽는다
HALL_OVERLAP = timedelta(minutes=30)  # 규칙(firestore.rules)이 기록의 시각을 서버 시계 ±10분까지 받는다 — 늦은 시계로 올라온 줄을 안 놓치게 겹쳐 읽는다
HALL_EPOCH = "2000-01-01T00:00:00Z"
# 디스코드 #명예의-전당-hall 알림 (2026-10-13): 새 기록이 제 던전(게임 전당의 갈래와 같다)에서 5등 안이면 웹훅으로 올린다.
# 웹훅 주소는 저장소 Secrets의 DISCORD_HALL_WEBHOOK — 없으면(맥에서 손으로 돌릴 때) 새 줄을 pending에 적어 두고 다음 번에 올린다
HALL_TOP = 5
HALL_TELL_AGE = timedelta(hours=24)  # 이보다 묵은 줄은 알리지 않는다(통째로 다시 읽은 날 옛 기록이 쏟아지지 않게)
HALL_TELL_MAX = 8  # 한 번에 올리는 글 수
HALL_EN = {"거인": "Giant", "곰사람": "Bear Folk", "광대": "Clown", "구렁이": "Serpent", "구미호의 굴": "Gumiho's Den", "까마귀": "Crow", "도깨비": "Dokkaebi",
           "도사": "Taoist", "돌하르방": "Dol Hareubang", "무당": "Shaman", "무사": "Warrior", "문어": "Octopus", "반요": "Half-Yokai",
           "버려진 은광": "Abandoned Silver Mine", "사냥꾼": "Hunter", "산사람": "Mountain Folk", "선인": "Immortal", "설화의 길": "Folktale Road",
           "술사": "Sorcerer", "승려": "Monk", "안개 늪": "Misty Marsh", "약초꾼": "Herbalist", "여우": "Fox", "옛 왕릉": "Old Royal Tomb", "이무기": "Imugi",
           "인간": "Human", "자객": "Assassin", "장사": "Strongman", "저승": "Underworld", "천손": "Heaven's Child", "퇴마사": "Exorcist", "풍수사": "Geomancer"}

# 검색 순위를 볼 낱말. 페이지(index.html)의 같은 표와 맞춘다
IOS_TERMS = {"kr": ["설화던전", "정통 로그라이크", "던전크롤", "던전", "턴제 로그라이크", "로그라이크", "한국 설화", "설화", "도깨비"],
             "us": ["Seolhwa Dungeon", "korean folklore", "traditional roguelike", "roguelike", "dungeon crawl", "roguelike rpg", "dokkaebi"],
             # 일본 (2026-10-21 일본어판 출시): 앱 이름과 일본어 등록정보 키워드(legal/app_store.md 4절)에서
             "jp": ["説話ダンジョン", "本格ローグライク", "ローグライク", "ダンジョン", "ターン制 RPG", "韓国 妖怪", "トッケビ", "九尾狐"]}
PLAY_TERMS = {"kr": ["설화던전", "한국 설화 게임", "정통 로그라이크", "던전크롤", "로그라이크", "설화"],
              "us": ["Seolhwa Dungeon", "korean folklore roguelike", "traditional roguelike", "roguelike"],
              "jp": ["説話ダンジョン", "本格ローグライク", "ローグライク", "韓国 昔話"]}
COUNTRIES = ["kr", "us", "jp"]
LANG = {"kr": "ko", "us": "en", "jp": "ja"}
CC_NAME = {"kr": "한국", "us": "미국", "jp": "일본"}
# 애플 차트 (무료, 기기·장르 번호). 웹 차트 페이지에 200위까지 실려 온다(2026-10-10) — 못 읽으면 공개 RSS(100위까지)로
CHARTS = [("kr", "iphone", 7014, "롤플레잉"), ("kr", "iphone", 7001, "액션"), ("kr", "iphone", 6014, "게임"), ("kr", "ipad", 7014, "롤플레잉"),
          ("us", "iphone", 7014, "롤플레잉"), ("us", "iphone", 6014, "게임"), ("jp", "iphone", 7014, "롤플레잉"), ("jp", "iphone", 6014, "게임")]
# 플레이 설명문에 장르 낱말이 들어갔는지 (2026-10-09: 옛 글엔 셋 다 0번이었다)
GENRE_WORDS = {"kr": ["로그라이크", "턴제", "RPG", "던전크롤", "도트"], "us": ["roguelike", "turn-based", "RPG", "dungeon crawl", "pixel"],
               "jp": ["ローグライク", "ターン制", "RPG", "ダンジョン", "ドット"]}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

errors = []


def get(url, headers=None, tries=3, data=None):
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def step(name, fn, *a):
    """한 갈래를 돌리고, 실패하면 errors에 남기고 None"""
    try:
        return fn(*a)
    except Exception as e:
        errors.append({"what": name, "why": "%s: %s" % (type(e).__name__, str(e)[:200])})
        traceback.print_exc()
        return None


def hangul(s):
    return bool(re.search("[가-힣]", s or ""))


def kana(s):
    return bool(re.search("[\u3040-\u30ff]", s or ""))


def lang_of(s):
    """글의 말: 한글이 있으면 ko, 가나가 있으면 ja, 둘 다 없으면 en"""
    return "ko" if hangul(s) else "ja" if kana(s) else "en"


# ---------------------------------------------------------------- 앱스토어

def ios_lookup(cc):
    d = get_json("https://itunes.apple.com/lookup?id=%d&country=%s" % (IOS_ID, cc))
    if not d.get("results"):
        return {"listed": False}
    r = d["results"][0]
    return {"listed": True, "name": r.get("trackName"), "version": r.get("version"),
            "updated": r.get("currentVersionReleaseDate"), "rating": r.get("averageUserRating"),
            "ratings": r.get("userRatingCount", 0), "english": not hangul(r.get("description", "")[:300]), "lang": lang_of(r.get("description", "")[:300]),
            "desc_head": (r.get("description") or "")[:140], "shots": len(r.get("screenshotUrls") or [])}


def ios_rank(cc, term):
    q = urllib.parse.urlencode({"term": term, "country": cc, "entity": "software", "limit": 200})
    rows = get_json("https://itunes.apple.com/search?" + q).get("results", [])
    for i, r in enumerate(rows, 1):
        if r.get("trackId") == IOS_ID:
            return i
    return None


def chart_key(cc, device, genre):
    """아이폰은 옛 열쇠 그대로(kr.7014) — history.jsonl과 이어지게. 아이패드만 뒤에 붙인다"""
    return "%s.%d" % (cc, genre) + ("" if device == "iphone" else "." + device)


def ios_chart_web(cc, device, genre):
    """앱스토어 웹 차트. 화면에는 25개만 그려지지만 페이지에 실린 JSON에 나머지 175개의 번호가 차례대로 들어 있다
    (segments[].shelves = 그려진 25개, segments[].nextPage.remainingContent = 그 뒤). 2026-10-09에 '50개만 준다'고 본 것은 그려진 것만 센 탓"""
    page = get("https://apps.apple.com/%s/%s/charts/%d" % (cc, device, genre), headers={"Accept-Language": "en-US,en;q=0.9"})
    for raw in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', page, re.S):
        try:
            segs = json.loads(raw)["data"][0]["data"]["segments"]
        except Exception:
            continue
        for seg in segs:
            if seg.get("chart") != "top-free":
                continue
            ids = []

            def walk(o):
                if isinstance(o, dict):
                    a = o.get("adamId")
                    if isinstance(a, str) and a not in ids:
                        ids.append(a)
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(seg.get("shelves"))
            for x in (seg.get("nextPage") or {}).get("remainingContent", []):
                if x.get("id") and x["id"] not in ids:
                    ids.append(x["id"])
            if len(ids) >= 50:
                return (ids.index(str(IOS_ID)) + 1 if str(IOS_ID) in ids else None), len(ids)
    raise RuntimeError("웹 차트에서 목록을 못 찾았다")


def ios_chart_rss(cc, device, genre):
    kind = "topfreeapplications" if device == "iphone" else "topfreeipadapplications"
    feed = get_json("https://itunes.apple.com/%s/rss/%s/limit=200/genre=%d/json" % (cc, kind, genre))["feed"]
    rows = feed.get("entry", [])
    if not rows:
        raise RuntimeError("빈 목록")
    for i, e in enumerate(rows, 1):
        if e["id"]["attributes"].get("im:id") == str(IOS_ID):
            return i, len(rows)
    return None, len(rows)


def ios_chart(cc, device, genre):
    """(순위 또는 None, 목록 길이)"""
    try:
        return ios_chart_web(cc, device, genre)
    except Exception as e:
        r = ios_chart_rss(cc, device, genre)
        errors.append({"what": "앱스토어 차트 %s %s %d (200위까지)" % (cc, device, genre), "why": "%s — 공개 목록(100위까지)으로 읽었다" % str(e)[:120]})
        return r


def ios_reviews(cc):
    d = get_json("https://itunes.apple.com/%s/rss/customerreviews/page=1/id=%d/sortby=mostrecent/json" % (cc, IOS_ID))
    out = []
    for e in d.get("feed", {}).get("entry", []) or []:
        if "im:rating" not in e:
            continue
        out.append({"rating": int(e["im:rating"]["label"]), "title": e["title"]["label"],
                    "text": e["content"]["label"][:400], "ver": e.get("im:version", {}).get("label", ""),
                    "date": e.get("updated", {}).get("label", "")[:10]})
    return out[:10]


# ---------------------------------------------------------------- 구글 플레이 (google-play-scraper)

def play_app(cc, lang):
    from google_play_scraper import app
    r = app(PLAY_ID, lang=lang, country=cc)
    desc = r.get("description") or ""
    words = GENRE_WORDS[cc]
    return {"title": r.get("title"), "installs": r.get("installs"), "real": r.get("realInstalls"),
            "score": round(r["score"], 2) if r.get("score") else None, "ratings": r.get("ratings") or 0,
            "reviews": r.get("reviews") or 0, "version": r.get("version"), "updated": r.get("updated"),
            "english": not hangul(desc[:300]), "lang": lang_of(desc[:300]), "desc_head": desc[:140],
            # 한국어는 띄어쓰기를 지우고 센다 — '던전 크롤'과 '던전크롤'을 같은 낱말로
            "genre_words": {w: (desc.replace(" ", "") if lang == "ko" else desc).lower().count(w.lower()) for w in words}}


def play_rank(cc, lang, term):
    """(순위 또는 None, 목록 길이). 검색 페이지에 실린 앱 링크의 차례 — google-play-scraper의 search는 맨 위 큰 카드의
    앱 번호를 비워 줄 때가 있다(2026-10-10 '던전크롤'). 비어 오면(구글이 잠깐 막을 때) 몇 초 쉬고 다시, 끝내 비면 scraper로"""
    url = "https://play.google.com/store/search?" + urllib.parse.urlencode({"q": term, "c": "apps", "hl": lang, "gl": cc.upper()})
    ids = []
    for i in range(3):
        try:
            page = get(url, headers={"Accept-Language": {"ko": "ko-KR,ko;q=0.9", "ja": "ja-JP,ja;q=0.9"}.get(lang, "en-US,en;q=0.9")}, tries=1)
            for m in re.finditer(r"/store/apps/details\?id=([\w.]+)", page):
                if m.group(1) not in ids:
                    ids.append(m.group(1))
        except Exception:
            pass
        if ids:
            break
        time.sleep(5 * (i + 1))
    if not ids:
        from google_play_scraper import search
        ids = [r.get("appId") for r in search(term, lang=lang, country=cc, n_hits=30)]
    if not ids:
        raise RuntimeError("빈 결과")
    return (ids.index(PLAY_ID) + 1 if PLAY_ID in ids else None), len(ids)


def play_reviews(cc, lang):
    from google_play_scraper import Sort, reviews
    rows, _ = reviews(PLAY_ID, lang=lang, country=cc, sort=Sort.NEWEST, count=10)
    out = []
    for r in rows:
        at = r.get("at")
        out.append({"rating": int(r.get("score") or 0), "title": "", "text": (r.get("content") or "")[:400],
                    "ver": r.get("reviewCreatedVersion") or r.get("appVersion") or "",
                    "date": at.astimezone(KST).strftime("%Y-%m-%d") if at else "", "replied": bool(r.get("replyContent"))})
    return out


# ---------------------------------------------------------------- 유튜브

def yt_api(path, **q):
    q["key"] = os.environ["YT_API_KEY"]
    return get_json("https://www.googleapis.com/youtube/v3/%s?%s" % (path, urllib.parse.urlencode(q)))


SEARCH_EVERY = 6  # 다른 채널 검색(search.list)은 한 번에 100단위라 여섯 시간에 한 번만. 나머지 호출은 한 번에 3단위 남짓


def youtube(prev):
    """prev = 지난 수집의 youtube. 키가 있어도 API가 실패하면(하루 한도 10,000단위를 넘기는 등) 키 없는 길로 내려간다"""
    if os.environ.get("YT_API_KEY"):
        try:
            return youtube_api(prev or {})
        except Exception as e:
            errors.append({"what": "유튜브 API", "why": "%s — 키 없이 읽었다" % str(e)[:160]})
    return youtube_rss(prev or {})


def youtube_api(prev):
    ch = yt_api("channels", part="snippet,statistics,contentDetails", forHandle=YT_HANDLE)["items"][0]
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], ""
    for _ in range(4):
        q = {"part": "contentDetails", "playlistId": up, "maxResults": 50}
        if tok:
            q["pageToken"] = tok
        d = yt_api("playlistItems", **q)
        ids += [x["contentDetails"]["videoId"] for x in d.get("items", [])]
        tok = d.get("nextPageToken", "")
        if not tok:
            break
    vids = []
    for i in range(0, len(ids), 50):
        for v in yt_api("videos", part="snippet,statistics", id=",".join(ids[i:i + 50])).get("items", []):
            s = v.get("statistics", {})
            vids.append({"id": v["id"], "title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"],
                         "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                         "comments": int(s.get("commentCount", 0))})
    others, others_at = prev.get("others"), prev.get("others_at")
    due = others is None or not others_at or datetime.now(KST) - datetime.fromisoformat(others_at) >= timedelta(hours=SEARCH_EVERY) - timedelta(minutes=10)
    if due:
        try:
            d = yt_api("search", part="snippet", q='설화던전|"Seolhwa Dungeon"', type="video", maxResults=25, order="date")
            others = [{"id": x["id"]["videoId"], "title": x["snippet"]["title"], "channel": x["snippet"]["channelTitle"],
                       "published": x["snippet"]["publishedAt"]} for x in d.get("items", [])
                      if x["snippet"]["channelId"] != ch["id"]]
            others_at = datetime.now(KST).isoformat(timespec="seconds")
        except Exception as e:
            errors.append({"what": "유튜브 다른 채널 검색", "why": str(e)[:200]})
    st = ch["statistics"]
    return {"via": "api", "channel_id": ch["id"], "subs": int(st.get("subscriberCount", 0)),
            "total_views": int(st.get("viewCount", 0)), "videos": vids, "others": others, "others_at": others_at}


YT_COOKIE = {"Cookie": "CONSENT=YES+cb; SOCS=CAI"}
OLD_REFRESH = 25  # RSS(최근 15편) 밖의 옛 영상은 영상 페이지를 하나씩 열어야 조회수가 나온다 — 한 번에 이만큼만, 오래 안 본 것부터


def yt_tab_ids(tab):
    page = get("https://www.youtube.com/@%s/%s?hl=en&gl=US" % (YT_HANDLE, tab), headers=YT_COOKIE)
    ids = []
    for m in re.finditer(r'"videoId":"([\w-]{11})"', page):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def yt_watch(vid):
    """영상 페이지에서 제목·조회수·올린 때·채널 이름"""
    page = get("https://www.youtube.com/watch?v=%s&hl=en&gl=US" % vid, headers=YT_COOKIE, tries=2)
    t = re.search(r'"videoDetails":\{"videoId":"%s","title":"((?:[^"\\]|\\.)*)"' % re.escape(vid), page)
    v = re.search(r'"videoDetails":\{.*?"viewCount":"(\d+)"', page, re.S)
    d = re.search(r'"publishDate":"([^"]+)"', page) or re.search(r'"uploadDate":"([^"]+)"', page)
    o = re.search(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"', page)
    if not v:
        raise RuntimeError("조회수를 못 찾았다")
    return {"id": vid, "title": json.loads('"%s"' % t.group(1)) if t else "", "views": int(v.group(1)),
            "published": d.group(1) if d else "", "owner": json.loads('"%s"' % o.group(1)) if o else ""}


def yt_others(prev):
    """다른 채널이 올린 영상: 유튜브 검색(최근순) 결과에서 제목에 게임 이름이 든 남의 영상. 여섯 시간에 한 번"""
    others, others_at = prev.get("others"), prev.get("others_at")
    due = others is None or not others_at or datetime.now(KST) - datetime.fromisoformat(others_at) >= timedelta(hours=SEARCH_EVERY) - timedelta(minutes=10)
    if not due:
        return others, others_at
    try:
        found = {}
        for q in ["설화던전", "Seolhwa Dungeon"]:
            page = get("https://www.youtube.com/results?search_query=%s&sp=CAI%%253D&hl=ko&gl=KR" % urllib.parse.quote(q), headers=YT_COOKIE)
            m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
            if not m:
                raise RuntimeError("검색 결과를 못 읽었다 ('%s')" % q)
            stack = [json.loads(m.group(1))]
            while stack:
                o = stack.pop()
                if isinstance(o, dict):
                    v = o.get("videoRenderer")
                    if isinstance(v, dict) and v.get("videoId"):
                        title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                        ch = "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", []))
                        if ch.lower() != YT_HANDLE and re.search(r"설화\s?던전|seolhwa", title, re.I):
                            found[v["videoId"]] = {"id": v["videoId"], "title": title, "channel": ch, "published": "",
                                                   "when": v.get("publishedTimeText", {}).get("simpleText", ""),
                                                   "views_text": v.get("viewCountText", {}).get("simpleText", "")}
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)
            time.sleep(2)
        return list(found.values()), datetime.now(KST).isoformat(timespec="seconds")
    except Exception as e:
        errors.append({"what": "유튜브 다른 채널 검색(키 없이)", "why": str(e)[:200]})
        return others, others_at


def yt_feed(q, now):
    """유튜브 RSS 한 장(최근 15편, 조회수가 들어 있다) → {영상 번호: 영상}"""
    xml = get("https://www.youtube.com/feeds/videos.xml?" + q, tries=2)
    vids = {}
    for ent in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", ent).group(1)
        title = re.search(r"<title>(.*?)</title>", ent, re.S).group(1)
        pub = re.search(r"<published>(.*?)</published>", ent).group(1)
        views = re.search(r'<media:statistics views="(\d+)"', ent)
        vids[vid] = {"id": vid, "title": unescape(title), "published": pub, "views": int(views.group(1)) if views else None, "checked": now}
    return vids


def youtube_rss(prev):
    """키 없이: 채널 페이지에서 채널 번호·구독자 → RSS(최근 15편, 조회수는 들어 있다)
    → 채널의 쇼츠·동영상 탭에서 나머지 영상 번호 → 영상 페이지에서 조회수(한 번에 OLD_REFRESH편씩, 오래 안 본 것부터)"""
    page = get("https://www.youtube.com/@%s?hl=en&gl=US" % YT_HANDLE, headers=YT_COOKIE)
    m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page)
    if not m:
        raise RuntimeError("채널 번호를 못 찾았다")
    cid = m.group(1)
    subs = None
    s = re.search(r'([\d][\d.,]*\s?[KM]?)\s+subscribers?\b', page)
    k = re.search(r"구독자\s*([\d.,]+)\s*(천|만)?\s*명", page)
    if s:
        n = s.group(1).replace(",", "").replace(" ", "")
        subs = int(float(n[:-1]) * (1000 if n[-1] == "K" else 1000000)) if n[-1] in "KM" else int(float(n))
    elif k:
        subs = int(float(k.group(1).replace(",", "")) * {"천": 1000, "만": 10000}.get(k.group(2) or "", 1))
    else:
        i = max(page.find("subscriber"), page.find("구독자"))
        errors.append({"what": "유튜브 구독자 수(키 없이)", "why": "채널 페이지에서 못 찾음 — 길이 %d, 주변: %s" % (len(page), page[max(0, i - 80):i + 40].replace("\n", " ") if i >= 0 else "없음")})
    now = datetime.now(KST).isoformat(timespec="seconds")
    vids, feed_ok = {}, True
    try:
        vids = yt_feed("channel_id=" + cid, now)
    except Exception as e:
        # 채널 RSS는 가끔 통째로 404다 (2026-10-12: 이것 하나로 유튜브 갈래가 다 비었다). 긴 영상·쇼츠 피드를 따로 불러 보고,
        # 거기에도 없는 영상은 아래에서 영상 페이지를 하나씩 열어 읽는다(이때는 한 번에 다 읽는다)
        feed_ok = False
        for pl in ["UULF", "UUSH"]:
            try:
                vids.update(yt_feed("playlist_id=" + pl + cid[2:], now))
            except Exception:
                pass
        errors.append({"what": "유튜브 채널 RSS", "why": "%s — 영상 페이지에서 대신 읽었다" % str(e)[:120]})
    # 옛 영상: 지난 수집에서 알던 것 + 채널 탭에 보이는 것
    old = {v["id"]: dict(v) for v in (prev.get("videos") or []) if v.get("id") and v["id"] not in vids}
    try:
        for tab in ["shorts", "videos"]:
            for vid in yt_tab_ids(tab):
                if vid not in vids and vid not in old:
                    old[vid] = {"id": vid, "title": "", "published": "", "views": None, "checked": ""}
            time.sleep(1)
    except Exception as e:
        errors.append({"what": "유튜브 채널의 옛 영상 목록(키 없이)", "why": str(e)[:160]})
    failed = 0
    for v in sorted(old.values(), key=lambda v: (v.get("views") is not None, v.get("checked") or ""))[:OLD_REFRESH if feed_ok else 4 * OLD_REFRESH]:
        try:
            w = yt_watch(v["id"])
            if w["owner"] and w["owner"].lower() != YT_HANDLE:  # 채널 탭에 섞여 온 남의 영상
                old.pop(v["id"], None)
                continue
            v.update({"title": w["title"] or v.get("title", ""), "published": w["published"] or v.get("published", ""), "views": w["views"], "checked": now})
        except Exception:
            failed += 1
        time.sleep(0.7)
    if failed:
        errors.append({"what": "유튜브 옛 영상 조회수(키 없이)", "why": "%d편을 못 읽어 지난 값을 그대로 뒀다" % failed})
    allv = list(vids.values()) + [v for v in old.values() if v.get("views") is not None]
    allv.sort(key=lambda v: v.get("published") or "", reverse=True)
    others, others_at = yt_others(prev)
    return {"via": "rss", "channel_id": cid, "subs": subs, "total_views": sum(v["views"] or 0 for v in allv), "videos": allv,
            "others": others, "others_at": others_at}


def unescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")


# ---------------------------------------------------------------- 명예의 전당 (Firestore, 읽기는 공개)

def fs_val(f):
    k, v = next(iter(f.items()))
    if k == "mapValue":
        return {a: fs_val(b) for a, b in v.get("fields", {}).items()}
    if k == "arrayValue":
        return [fs_val(x) for x in v.get("values", [])]
    if k == "integerValue":
        return int(v)
    return v


# 읽기 한도 (2026-10-12): Firestore 무료 요금제는 하루 읽기 5만 건이고 "돌려준 문서 수"로 센다. 옛 수집기와 페이지는
# 돌 때마다 hall을 통째로(기록 수만큼) 읽어, 기록이 320줄을 넘긴 날 한도를 다 쓰고 게임 안 전당까지 429로 멎었다.
# 그래서 읽은 줄을 HALL_CACHE에 모아 두고, 마지막으로 본 시각 뒤에 올라온 줄만 읽는다(한 번에 몇 건).
# 줄 수가 서버와 어긋나면(관리자가 지운 기록·놓친 줄) 하루에 한 번까지만 통째로 다시 읽는다.

def fs_post(method, body):
    return get_json("%s:%s?key=%s" % (FS_DOCS, method, FS_KEY), data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def hall_where(since):
    return {"fieldFilter": {"field": {"fieldPath": "at"}, "op": "GREATER_THAN_OR_EQUAL", "value": {"timestampValue": since}}}


def hall_row(d):
    r = {k: fs_val(v) for k, v in d.get("fields", {}).items()}
    if not isinstance(r.get("at"), str):
        return None
    story = r["detail"].get("story") if isinstance(r.get("detail"), dict) else None  # 남이 꾸민 기록이면 형이 엉뚱할 수 있다
    story = " ".join(x for x in story if isinstance(x, str)) if isinstance(story, list) else ""
    return {"id": d.get("name", "").split("/")[-1], "at": r["at"], "uid": r.get("uid", ""), "name": r.get("name", ""), "depth": r.get("depth", 0),
            "dungeon": r.get("dungeon", ""), "by": r.get("by", ""), "ver": r.get("ver", ""), "species": r.get("species", ""), "cls": r.get("cls", ""),
            "win": r.get("win") is True, "level": r.get("level", 0), "score": r.get("score", 0),
            "en": bool(story) and lang_of(story) == "en", "ja": bool(story) and lang_of(story) == "ja"}  # 일본어 판은 한글이 없어 옛 셈에선 영어로 잡혔다


def hall_since(since):
    """시각이 since 이후인 기록을 시각 차례로. 돌려받은 줄 수만큼만 읽기 한도를 쓴다"""
    fields = ["uid", "name", "at", "depth", "dungeon", "by", "ver", "species", "cls", "win", "level", "score", "detail.story"]
    out, seen = [], set()
    while True:
        q = {"from": [{"collectionId": "hall"}], "where": hall_where(since), "orderBy": [{"field": {"fieldPath": "at"}, "direction": "ASCENDING"}],
             "limit": 300, "select": {"fields": [{"fieldPath": f} for f in fields]}}
        docs = [e["document"] for e in fs_post("runQuery", {"structuredQuery": q}) if isinstance(e, dict) and "document" in e]
        fresh = 0
        for d in docs:
            row = hall_row(d)
            if row and row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
                fresh += 1
        if len(docs) < 300 or not fresh:
            return out
        since = out[-1]["at"]  # 다음 쪽: 마지막 줄의 시각부터(같은 초의 줄은 번호로 거른다)


def hall_count():
    """서버의 기록 수. 개수 세기는 색인 1,000줄에 읽기 1건이다"""
    res = fs_post("runAggregationQuery", {"structuredAggregationQuery": {
        "structuredQuery": {"from": [{"collectionId": "hall"}], "where": hall_where(HALL_EPOCH)}, "aggregations": [{"alias": "n", "count": {}}]}})
    return int(res[0]["result"]["aggregateFields"]["n"]["integerValue"])


def hall_time(at):
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def hall_text(s, n=40):
    """남이 적은 글자(이름·사인)를 디스코드 글에 넣기 전에: 꾸밈 기호·멘션·줄바꿈을 걷는다"""
    return re.sub(r"[\\*_~`|>@#\[\]()<:/\s]+", " ", str(s)).strip()[:n]


def hall_tell(rows, pending):
    """pending(새로 읽은 기록 번호) 가운데 제 던전 5등 안인 것을 디스코드에 올리고, 아직 못 올린 번호를 돌려준다"""
    hook = os.environ.get("DISCORD_HALL_WEBHOOK", "").strip()
    now = datetime.now(timezone.utc)
    fresh = [rows[i] for i in pending if i in rows and now - hall_time(rows[i]["at"]) <= HALL_TELL_AGE]
    if not hook:
        return [r["id"] for r in fresh]
    # 5등 안을 먼저 고르고 나서 수를 자른다 — 거꾸로 하면 새 줄이 많이 쌓인 날 5등 안 기록이 잘려 나가 영영 안 올라갔다(2026-10-14, 45줄 중 마지막 8줄만 보고 다섯을 잃음)
    cand = []
    for r in sorted(fresh, key=lambda r: hall_time(r["at"])):
        if not isinstance(r.get("score"), int) or isinstance(r.get("score"), bool):
            continue
        rank = 1 + sum(1 for o in rows.values() if o.get("dungeon") == r.get("dungeon") and isinstance(o.get("score"), int) and o["score"] > r["score"])
        if rank <= HALL_TOP:
            cand.append((r, rank))
    left = [r["id"] for r, _ in cand[HALL_TELL_MAX:]]  # 넘친 것은 다음 번에
    for r, rank in cand[:HALL_TELL_MAX]:
        dn, sp, cl, by = [hall_text(r.get(k, "")) for k in ["dungeon", "species", "cls", "by"]]
        end = "정복" if r.get("win") else ("%s에게 쓰러짐" % by if by else "쓰러짐")
        ko = "%s %s · 레벨 %s · %s층 · %s점 · %s" % (sp, cl, hall_text(r.get("level")), hall_text(r.get("depth")), format(r["score"], ","), end)
        en = "%s %s · Lv %s · F%s · %s pts · %s" % (HALL_EN.get(sp, sp), HALL_EN.get(cl, cl), hall_text(r.get("level")), hall_text(r.get("depth")),
                                                   format(r["score"], ","), "Conquered" if r.get("win") else "Fallen")
        body = {"allowed_mentions": {"parse": []}, "embeds": [{
            "title": "%s %d위 · %s" % (dn, rank, hall_text(r.get("name"), 16) or "?"),
            "description": "%s\n%s · %s #%d" % (ko, en, HALL_EN.get(dn, dn), rank),
            "color": 0xE8BD45 if rank == 1 else 0x8C6A3F, "timestamp": r["at"], "footer": {"text": "v%s" % hall_text(r.get("ver"), 12)}}]}
        try:
            get(hook + "?wait=true", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, tries=1)
            time.sleep(1)
        except Exception as e:
            left.append(r["id"])
            errors.append({"what": "디스코드 전당 알림", "why": type(e).__name__})  # 주소가 새지 않게 까닭은 종류만
    return left


def hall(out):
    path = os.path.join(out, HALL_CACHE)
    rows, full, pending, had, stale_ids = {}, "", [], False, set()
    try:
        c = json.load(open(path))
        rows = {r[0]: dict(zip(c["cols"], r)) for r in c["rows"]}
        full = c.get("full", "")
        if c.get("cols") != HALL_COLS:  # 칸이 늘었다(2026-10-21 ja): 아래에서 통째로 다시 읽는다. 알던 줄 번호는 남겨 디스코드에 옛 기록을 다시 알리지 않는다
            stale_ids = set(rows)
            rows, full = {}, ""
        pending = [i for i in c.get("pending", []) if isinstance(i, str)]
        had = True
    except Exception:
        pass
    known = set(rows) | stale_ids
    today = datetime.now(KST).date().isoformat()
    try:
        if rows:
            last = max(hall_time(r["at"]) for r in rows.values())
            for r in hall_since((last - HALL_OVERLAP).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")):
                rows[r["id"]] = r
        if not rows or (hall_count() != len(rows) and full != today):
            rows = {r["id"]: r for r in hall_since(HALL_EPOCH)}
            full = today
    except Exception as e:
        if not rows:
            raise
        # 새 줄을 못 읽었으면(한도 초과 등) 모아 둔 줄로 요약한다 — 숫자가 비지 않고 조금 낡을 뿐이다
        errors.append({"what": "명예의 전당 새 기록", "why": ("%s: %s" % (type(e).__name__, e))[:200]})
    body = sorted(rows.values(), key=lambda r: (hall_time(r["at"]), r["id"]))
    if had:  # 모아 둔 것이 없던 첫 판에는 알리지 않는다(전부가 새 줄이다)
        pending = step("디스코드 전당 알림", hall_tell, rows, pending + [i for i in rows if i not in known and i not in pending]) or []
    json.dump({"at": datetime.now(KST).isoformat(timespec="seconds"), "full": full, "pending": pending, "cols": HALL_COLS, "rows": [[r.get(k) for k in HALL_COLS] for r in body]},
              open(path, "w"), ensure_ascii=False, separators=(",", ":"))
    return hall_summary(body)


def hall_summary(cache):
    rows = []
    for r in cache:
        t = hall_time(r["at"]).astimezone(KST)
        if t >= LAUNCH:
            rows.append(dict(r, t=t))
    rows.sort(key=lambda r: r["t"])
    today = datetime.now(KST).date()
    first = {}
    for r in rows:
        first.setdefault(r["uid"], r["t"].date())
    days = {}
    for r in rows:
        k = r["t"].date().isoformat()
        e = days.setdefault(k, {"runs": 0, "people": set(), "new": set()})
        e["runs"] += 1
        e["people"].add(r["uid"])
        if first[r["uid"]] == r["t"].date():
            e["new"].add(r["uid"])
    tr = [r for r in rows if r["t"].date() == today]
    back = sum(1 for u, f in first.items() if f < today and len({r["t"].date() for r in rows if r["uid"] == u}) > 1)
    who = {}
    for r in tr:
        e = who.setdefault(r["uid"], {"name": r["name"], "runs": 0, "new": first[r["uid"]] == today})
        e["runs"] += 1
        e["name"] = r["name"]
        e["id"] = r["id"]
    lost = [r for r in rows if not r["win"]]
    spots, causes = {}, {}
    for r in lost:
        k = "%s %s층" % (r["dungeon"], r["depth"])
        spots[k] = spots.get(k, 0) + 1
        causes[r["by"] or "?"] = causes.get(r["by"] or "?", 0) + 1
    top = lambda d: [{"name": k, "n": n} for k, n in sorted(d.items(), key=lambda kv: -kv[1])[:6]]
    return {"total_runs": len(rows), "total_people": len(first), "today_runs": len(tr),
            "today_people": len({r["uid"] for r in tr}), "today_new": sum(1 for f in first.values() if f == today),
            "en_runs": sum(bool(r.get("en")) for r in rows), "ja_runs": sum(bool(r.get("ja")) for r in rows), "wins": sum(1 for r in rows if r["win"]),
            "returned": back, "returned_of": sum(1 for f in first.values() if f < today),
            "today": sorted(who.values(), key=lambda e: -e["runs"]), "lost": len(lost), "spots": top(spots), "causes": top(causes),
            "days": [{"day": k, "runs": v["runs"], "people": len(v["people"]), "new": len(v["new"])} for k, v in sorted(days.items())],
            # id = 전당 문서 번호. 페이지가 그 줄을 누르면 이 번호로 문서를 통째로 읽어 마지막 모습을 보인다
            "recent": [{"id": r["id"], "at": r["t"].isoformat(), "name": r["name"], "who": "%s %s" % (r["species"], r["cls"]),
                        "dungeon": r["dungeon"], "depth": r["depth"], "by": r["by"], "ver": r["ver"], "win": r["win"]}
                       for r in rows[-20:][::-1]]}


# ---------------------------------------------------------------- 묶기

def metrics(s):
    """history.jsonl 한 줄에 들어갈 숫자들. 열쇠는 페이지(index.html)의 LABEL과 맞춘다"""
    m = {}
    h = s.get("hall") or {}
    for k in ["total_runs", "total_people", "today_runs", "today_people", "today_new", "en_runs", "ja_runs"]:
        if k in h:
            m["hall." + k] = h[k]
    for cc in COUNTRIES:
        a = (s.get("ios") or {}).get(cc) or {}
        if a.get("listed"):
            m["ios.%s.ratings" % cc] = a.get("ratings", 0)
            if a.get("rating") is not None:
                m["ios.%s.rating" % cc] = round(a["rating"], 2)
        p = (s.get("play") or {}).get(cc) or {}
        if p.get("real") is not None:
            m["play.%s.real" % cc] = p["real"]
        if p.get("ratings") is not None and p:
            m["play.%s.ratings" % cc] = p["ratings"]
    for k, v in (s.get("ios_rank") or {}).items():
        m["ios.rank." + k] = v
    for k, v in (s.get("play_rank") or {}).items():
        m["play.rank." + k] = v
    for k, v in (s.get("charts") or {}).items():
        m["chart." + k] = v
    y = s.get("youtube") or {}
    if y.get("subs") is not None:
        m["yt.subs"] = y["subs"]
    for v in y.get("videos") or []:
        if v.get("views") is not None:
            m["yt.v." + v["id"]] = v["views"]
    return m


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    s = {"at": datetime.now(KST).isoformat(timespec="seconds")}
    prev = {}
    try:
        prev = json.load(open(os.path.join(out, "latest.json")))
    except Exception:
        pass
    s["hall"] = step("명예의 전당", hall, out)
    s["ios"] = {cc: step("앱스토어 %s 페이지" % cc, ios_lookup, cc) for cc in COUNTRIES}
    s["ios_rank"] = {}
    for cc, terms in IOS_TERMS.items():
        for t in terms:
            try:
                s["ios_rank"]["%s.%s" % (cc, t)] = ios_rank(cc, t)
            except Exception as e:
                errors.append({"what": "앱스토어 검색 %s '%s'" % (cc, t), "why": str(e)[:200]})
            time.sleep(1)
    s["charts"], s["charts_n"] = {}, {}
    for cc, device, g, name in CHARTS:
        k = chart_key(cc, device, g)
        try:
            s["charts"][k], s["charts_n"][k] = ios_chart(cc, device, g)
        except Exception as e:
            errors.append({"what": "앱스토어 차트 %s %s %s" % (cc, device, name), "why": str(e)[:200]})
        time.sleep(1)
    s["reviews"] = {cc: step("앱스토어 %s 리뷰" % cc, ios_reviews, cc) for cc in COUNTRIES}
    for cc in COUNTRIES:  # 애플 리뷰 목록은 가끔 비어 온다 — 그럴 땐 지난 목록을 그대로 둔다
        old = (prev.get("reviews") or {}).get(cc)
        if not s["reviews"][cc] and old:
            s["reviews"][cc] = old
    s["play"], s["play_reviews"] = {}, {}
    for cc in COUNTRIES:
        s["play"][cc] = step("플레이 %s 페이지" % CC_NAME[cc], play_app, cc, LANG[cc])
        time.sleep(2)
    for cc in COUNTRIES:
        s["play_reviews"][cc] = step("플레이 %s 리뷰" % CC_NAME[cc], play_reviews, cc, LANG[cc])
        time.sleep(2)
    for cc in COUNTRIES:
        old = (prev.get("play_reviews") or {}).get(cc)
        if not s["play_reviews"][cc] and old:
            s["play_reviews"][cc] = old
    s["play_rank"], s["play_rank_n"] = {}, {}
    for cc, terms in PLAY_TERMS.items():
        for t in terms:
            time.sleep(3)  # 몰아서 읽으면 구글이 503으로 막는다 (2026-10-09)
            try:
                k = "%s.%s" % (cc, t)
                s["play_rank"][k], s["play_rank_n"][k] = play_rank(cc, LANG[cc], t)
            except Exception as e:
                errors.append({"what": "플레이 검색 %s '%s'" % (cc, t), "why": str(e)[:200]})
    s["youtube"] = step("유튜브", youtube, prev.get("youtube"))
    for k in ["hall", "youtube"]:  # 통째로 못 읽은 갈래는 지난 값을 둔다(errors에는 남는다) — 비우면 페이지의 그 칸이 비고, 유튜브는 옛 영상 목록까지 잃는다
        if s[k] is None and prev.get(k):
            s[k] = prev[k]
    s["errors"] = errors
    s["m"] = metrics(s)
    json.dump(s, open(os.path.join(out, "latest.json"), "w"), ensure_ascii=False, default=str)
    hp = os.path.join(out, "history.jsonl")
    lines = open(hp).read().splitlines() if os.path.exists(hp) else []
    lines.append(json.dumps({"at": s["at"], "m": s["m"]}, ensure_ascii=False, separators=(",", ":")))
    open(hp, "w").write("\n".join(lines[-KEEP:]) + "\n")
    print("수집 끝: 값 %d개, 못 읽은 것 %d개" % (len(s["m"]), len(errors)))
    for e in errors:
        print("  못 읽음 · %s · %s" % (e["what"], e["why"]))


if __name__ == "__main__":
    main()
