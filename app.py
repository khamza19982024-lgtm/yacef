from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import requests
from bs4 import BeautifulSoup
import re
import json
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, abort

# --------------------------------------
# معلومات حالة المباراة
STATUS_MAP = {
    "0": "لم تبدا",
    "1": "الشوط الاول",
    "2": "منتصف المباراة",
    "3": "الشوط الثاني",
    "4": "انتهت المباراة",
    "5": "الانتقال الى الاشواط الاضافية",
    "7": "الشوط الإضافي الأول",
    "8": "نهاية الشوط الإضافي الأول",
    "9": "الشوط الإضافي الثاني",
    "10": "نهاية الشوط الإضافي الثاني",
    "11": "ركلات الترجيح",
    "12": "توقف المباراة"
}
NOT_LIVE = {"0", "4", "12"}

# ترتيب توقفات وأحداث الشوط
STOP_ORDER = [
    "بدأت المباراة",
    "منتصف المباراة",
    "الشوط الإضافي الأول",
    "الشوط الإضافي الثاني",
    "نهاية الشوط الإضافي الثاني",
    "إنتهت المباراة",
]

# ------------------- دوال عامة -------------------

def compute_time_expr(status_value, time_value):
    if not time_value or time_value == "0":
        return None
    try:
        minutes = int(time_value.split(":")[0])
    except:
        return None

    if status_value == "1":
        base = 45
    elif status_value == "3":
        base = 90
    elif status_value == "7":
        base = 105
    elif status_value == "9":
        base = 120
    else:
        return str(minutes + 1)

    if minutes >= base:
        extra = (minutes - base) + 1
        return f"{base}+{extra}"
    else:
        return str(minutes + 1)

def parse_time_parts(time_str):
    if not time_str:
        return (-1, -1)
    match = re.match(r"(\d+)(?:\+(\d+))?", str(time_str))
    if match:
        base = int(match.group(1))
        extra = int(match.group(2)) if match.group(2) else 0
        return (base, extra)
    return (-1, -1)

def time_in_range(t, start, end):
    base, _ = parse_time_parts(t)
    return start <= base <= end

def clean_name(text):
    return re.split(r"[\(\n]", text)[0].strip()

def to_number(value: str):
    value = value.strip().replace("%", "")
    if value.isdigit():
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value

def change_logo_size(url):
    url = url.strip()
    if "teams/64/" in url:
        return url.replace("teams/64/", "teams/128/")
    return url
# ------------------- تنسيق وقت البداية الجديد -------------------

def format_match_start_time(raw_time_str):
    """يضيف 8 ساعات ويحول وقت البداية إلى صيغة 24 ساعة."""
    if not raw_time_str:
        return None
    
    try:
        # استبدال النصوص العربية بما يقابلها بالإنجليزية في التنسيق (لتوافق Python)
        time_str_en = raw_time_str.replace("مساءً", "PM").replace("صباحاً", "AM")
        
        # محاولة تحليل التاريخ والوقت
        if "PM" in time_str_en or "AM" in time_str_en:
            # تنسيق 12 ساعة مع التاريخ: YYYY-MM-DD HH:MM AM/PM
            dt = datetime.strptime(time_str_en, "%Y-%m-%d %I:%M %p")
        else:
            # تنسيق 24 ساعة مع التاريخ: YYYY-MM-DD HH:MM
            dt = datetime.strptime(raw_time_str, "%Y-%m-%d %H:%M")

        dt += timedelta(hours=8)
        return dt.strftime("%Y-%m-%d %H:%M") # إرجاع بصيغة 24 ساعة
    except Exception:
        return raw_time_str

def extract_start_time_raw(soup):
    """يستخلص وقت البداية الخام من صفحة المباراة."""
    time_div = soup.select_one(".time-title")
    if time_div:
        # مثال: "2025-08-05 03:30 مساءً"
        return time_div.get_text(strip=True)
    return None

# ------------------- استخراج الإحصائيات -------------------

def extract_stats(soup):
    stats = {}
    stats_section = soup.select_one("div.tab-content-item.inner-match-tab-content.stats")
    if not stats_section:
        return stats

    possession = stats_section.select_one(".progress-wrapper")
    if possession:
        home = to_number(possession.select_one(".team-a").get_text())
        away = to_number(possession.select_one(".team-b").get_text())
        stats["الاستحواذ"] = {"home": home, "away": away}

    for item in stats_section.select(".progress-state-item"):
        title = item.select_one(".title").get_text(strip=True)
        spans = item.select(".text span")
        if len(spans) >= 3:
            home = to_number(spans[0].get_text())
            away = to_number(spans[2].get_text())
            stats[title] = {"home": home, "away": away}
    return stats

# ------------------- استخراج الأحداث -------------------

def extract_match_events(soup, home_team_class="for-team-a", away_team_class="for-team-b"):
    unique_events = set()
    match_events = soup.find_all('div', class_='match-event-item')

    for event_item in match_events:
        event_data = {}
        classes = event_item.get('class', [])
        if home_team_class in classes:
            event_data['team'] = 'Home'
        elif away_team_class in classes:
            event_data['team'] = 'Away'
        else:
            continue

        event_link = event_item.find('a', attrs={"event_name": True})
        if not event_link:
            continue
        event_name = event_link.get("event_name")

        if event_name == "بطاقة صفراء":
            event_data['type'] = 'yellow card'
            event_data['player_name'] = event_link.get("player_a")
        elif event_name == "تبديل لاعب":
            event_data = {
                "team": event_data['team'],
                "type": 'substitution',
                "player_in": event_link.get("player_s"),
                "player_out": event_link.get("player_a"),
            }
        elif event_name in ["هدف", "ضربة جزاء", "هدف في مرماه"]:
            if event_name == "هدف":
                event_data['type'] = 'goal'
            elif event_name == "ضربة جزاء":
                event_data['type'] = 'goal_penalty'
            elif event_name == "هدف في مرماه":
                event_data['type'] = 'own_goal'
            event_data['player_name'] = event_link.get("player_a")
            event_data['assist'] = event_link.get("player_s") or None
        elif event_name == "بطاقة حمراء":
            yellow_icon = event_item.find('path', {"fill": "#ffda46"})
            event_data['type'] = 'second yellow red card' if yellow_icon else 'red card'
            event_data['player_name'] = event_link.get("player_a")
        else:
            continue

        time_element = event_item.find('div', class_='time')
        event_data['time'] = time_element.get_text(strip=True).replace('’', '') if time_element else None

        # Only add if we have essential data
        if event_data.get('time') or event_data.get('type'):
            unique_events.add(frozenset((k, v) for k, v in event_data.items() if v is not None))

    events = [dict(e) for e in unique_events]
    events.sort(key=lambda e: parse_time_parts(e.get("time", "")))
    return events

# ------------------- ركلات الترجيح -------------------

def parse_penalties(soup):
    pens_block = soup.select_one(".match-event-item.penalties")
    if not pens_block:
        return None
        
    pens_score = ""
    result_div = pens_block.select_one(".result")
    if result_div:
        raw_text = result_div.get_text(strip=True)
        scores = re.findall(r'\d+', raw_text)
        if len(scores) >= 2:
            pens_score = f"{scores[0]} - {scores[1]}"
        elif scores:
            pens_score = scores[0]
        
    pens_data = {
        "type": "pens",
        "name": "ركلات الترجيح",
        "pens_score": pens_score,
        "PenaltyTakers": {}
    }
    
    for team_div in pens_block.select(".team-item"):
        classes = team_div.get("class", [])
        team_name = "Home" if "team-a" in classes else "Away"
        names = [clean_name(li.get_text()) for li in team_div.select("ol.shots-text li")]
        shots = []
        for a in team_div.select(".p-shot-item"):
            cls = a.get("class", [])
            shots.append("scored" if "success" in cls else "missed")
        pens_data["PenaltyTakers"][team_name] = [{"player": n, "result": s} for n, s in zip(names, shots)]
        
    return pens_data

# ------------------- التوقفات -------------------

def extract_time_stops(soup):
    time_stops = []
    for item in soup.select("div.match-event-item.start-end-match"):
        title_el = item.select_one("span.title")
        if not title_el:
            continue
        text = title_el.get_text(strip=True)
        if "’" in text:
            time_part, name_part = text.split("’", 1)
            stop_event = {"type": "stop", "time": time_part.strip(), "name": name_part.strip()}
            time_stops.append(stop_event)
    return time_stops

def extract_match_stops(soup):
    stops_by_name = {}
    for item in soup.select("div.match-event-item.start-end-match"):
        title_el = item.select_one("span.title")
        if not title_el:
            continue
        text = title_el.get_text(strip=True)
        if "’" in text:
            continue
        name = text
        if name not in STOP_ORDER:
            continue
        stop_data = {"type": "stop", "name": name}
        score_el = item.select_one("div.m-result")
        if score_el:
            raw = score_el.get_text(" ", strip=True)
            score_text = re.sub(r"\s*-\s*", " - ", raw)
            score_text = re.sub(r"\s+", " ", score_text).strip()
            stop_data["score"] = score_text
        stops_by_name[name] = stop_data
    return stops_by_name

# ------------------- معلومات اللقاء -------------------

def extract_meeting_info(soup):
    match_info = {}
    blocks = soup.find_all("div", class_="match-block-item pt-2")
    for block in blocks:
        section_title_div = block.find("div", class_="section-title")
        if section_title_div and section_title_div.get_text(strip=True) == "معلومات اللقاء":
            info_items = block.find_all("div", class_="match-info-item")
            for item in info_items:
                title_div = item.find("div", class_="title")
                content_div = item.find("div", class_="content")
                if title_div and content_div:
                    for a in content_div.find_all("a"):
                        a.unwrap()
                    title = title_div.get_text(strip=True)
                    content = content_div.get_text(strip=True)
                    match_info[title] = content
            break
    return match_info

# ------------------- استخراج معلومات المباراة -------------------

# دالة format_match_time الأصلية (تم الاحتفاظ بها لعدم التعديل عليها، لكنها لم تعد تستخدم في extract_info للمباريات غير الحية)
def format_match_time(raw_time):
    try:
        # هذه الدالة مخصصة لـ "وقت الشوط الحالي" فقط، وليس وقت بداية المباراة
        dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
        dt += timedelta(hours=8)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw_time

# ------------------- استخراج معلومات المباراة (محدثة) -------------------
def extract_info(soup, teams_info):
    info = teams_info.copy()

    tag_status = soup.find("input", {"id": "match_status"})
    status_value = tag_status["value"] if tag_status and tag_status.has_attr("value") else None
    status_text = STATUS_MAP.get(status_value, "غير معروف")
    is_live = "False" if status_value in NOT_LIVE else "True"

    tag_time = soup.find("input", {"id": "match_time"})
    time_value = tag_time["value"] if tag_time and tag_time.has_attr("value") else None

    # الوقت الحالي في المباراة (مثل 45+2)
    current_match_time = compute_time_expr(status_value, time_value)

    info.update({
        "StartTime": teams_info.get("StartTime"), # وقت البداية المنسق الجديد
        "CurrentTime": current_match_time,       # وقت الشوط الحالي
        "Status": status_text,
        "Is live": is_live,
        "HomeScore": None,
        "AwayScore": None,
        "HomeAgg": None,
        "AwayAgg": None,
        "HomePen": None,
        "AwayPen": None,
        "Winner": ""
    })
    
    # تم حذف حقل "Time" واستبداله بـ "StartTime" و "CurrentTime"
    
    meeting_info = extract_meeting_info(soup)
    info.update(meeting_info)

    match_div = soup.find("div", class_="match-details")
    if match_div:
        main_result = match_div.find("div", class_="main-result")
        if main_result:
            b_tags = main_result.find_all("b")
            if len(b_tags) >= 2:
                info["HomeScore"] = b_tags[0].text.strip()
                info["AwayScore"] = b_tags[1].text.strip()

        agg_result = match_div.find("div", class_="other-result agg live-match-agg")
        if agg_result:
            b_tags = agg_result.find_all("b")
            if len(b_tags) >= 2:
                info["HomeAgg"] = b_tags[0].text.strip()
                info["AwayAgg"] = b_tags[1].text.strip()

        pen_result = match_div.find_all("div", class_="other-result")
        for div in pen_result:
            span = div.find("span")
            if span and "ركلات الترجيح" in span.text:
                b_tags = div.find_all("b")
                if len(b_tags) >= 2:
                    info["HomePen"] = b_tags[0].text.strip()
                    info["AwayPen"] = b_tags[1].text.strip()
                    break

        winner = ""
        if status_value in {"4", "12"}:
            win_tag = match_div.find("b", class_="win")
            if win_tag:
                parent_div = win_tag.find_parent("div")
                if parent_div:
                    b_tags = parent_div.find_all("b")
                    if len(b_tags) >= 2:
                        winner = "Home" if win_tag == b_tags[0] else "Away"
        info["Winner"] = winner

    return info


# ------------------- الدمج والترتيب -------------------

def build_match_info(html_content, teams_info):
    soup = BeautifulSoup(html_content, "html.parser")

    info = extract_info(soup, teams_info)
    stats = extract_stats(soup)
    events = extract_match_events(soup)
    time_stops = extract_time_stops(soup)
    events.extend(time_stops)
    events.sort(key=lambda e: parse_time_parts(e.get("time", "")))
    stops = extract_match_stops(soup)
    pens = parse_penalties(soup)

    output = []

    def add_events_in_range(start, end):
        part_events = [e for e in events if e.get("time") and time_in_range(e["time"], start, end)]
        output.extend(part_events)

    for name in STOP_ORDER:
        if name == "بدأت المباراة" and name in stops:
            output.append(stops[name])
            add_events_in_range(1, 45)
        elif name == "منتصف المباراة" and name in stops:
            output.append(stops[name])
            add_events_in_range(46, 90)
        elif name == "الشوط الإضافي الأول" and name in stops:
            output.append(stops[name])
            add_events_in_range(91, 105)
        elif name == "الشوط الإضافي الثاني" and name in stops:
            output.append(stops[name])
            add_events_in_range(106, 120)
        elif name == "نهاية الشوط الإضافي الثاني" and name in stops:
            output.append(stops[name])

    if pens:
        output.append(pens)
    if "إنتهت المباراة" in stops:
        output.append(stops["إنتهت المباراة"])

    return {
        "Info": info,
        "stats": stats,
        "events": list(reversed(output))
    }

# ------------------- Main API Function (محدثة) -------------------

def get_match_data(match_id: str):
    base_url = "https://www.ysscores.com"
    match_page_url = f"{base_url}/ar/match/{match_id}/dummy"
    api_url = f"{base_url}/ar/get_match_detail?match_id={match_id}"

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # Step 1: Get team names, logos, and start time
    try:
        page_resp = requests.get(match_page_url, headers=headers, timeout=10)
        page_resp.raise_for_status()
    except Exception as e:
        raise Exception(f"Failed to load match page: {e}")

    soup_page = BeautifulSoup(page_resp.text, "html.parser")
    teams = soup_page.select(".team-item")
    if len(teams) < 2:
        # قد تكون الصفحة غير صالحة، نحاول استخلاص وقت البداية على الأقل
        pass 

    def get_team_data(el):
        name_el = el.find("h3")
        name = name_el.get_text(strip=True) if name_el else el.find("img").get("title", "").strip()
        logo = el.find("img").get("src", "").strip()
        logo = change_logo_size(logo)
        return name, logo

    # استخلاص معلومات الفريق
    home_name, home_logo = get_team_data(teams[0]) if len(teams) >= 1 else ("", "")
    away_name, away_logo = get_team_data(teams[1]) if len(teams) >= 2 else ("", "")

    # استخلاص وتنسيق وقت البداية
    raw_start_time = extract_start_time_raw(soup_page)
    formatted_start_time = format_match_start_time(raw_start_time)

    teams_info = {
        "HomeTeam": home_name,
        "HomeImgLink": home_logo,
        "AwayTeam": away_name,
        "AwayImgLink": away_logo,
        "StartTime": formatted_start_time # إضافة وقت البداية المنسق
    }

    # Step 2: Get live match data
    try:
        api_resp = requests.get(api_url, headers=headers, timeout=10)
        api_resp.raise_for_status()
    except Exception as e:
        raise Exception(f"Failed to load match API: {e}")

    # Step 3: Build full data
    try:
        return build_match_info(api_resp.text, teams_info)
    except Exception as e:
        raise Exception(f"Failed to parse match data: {e}")

# ------------------- FastAPI Endpoint -------------------

app = Flask(__name__)

@app.route("/match/<match_id>", methods=["GET"])
def get_match(match_id):
    if not match_id.isdigit():
        abort(400, description="Invalid match ID. Must be numeric.")
    try:
        data = get_match_data(match_id)  # نفس الدالة التي كتبتها
        return jsonify(data)
    except Exception as e:
        abort(500, description=str(e))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
