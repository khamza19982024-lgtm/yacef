from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import re
from pydantic import BaseModel

app = FastAPI(title="Algerian League Scraper API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Match(BaseModel):
    match_id: str
    home_team: str
    away_team: str
    home_logo: str
    away_logo: str
    home_score: Optional[str] = None
    away_score: Optional[str] = None
    status: str  # "upcoming", "live", "finished", "postponed"
    match_time: Optional[str] = None
    date: str
    round: str
    match_url: str
    live_minute: Optional[str] = None
    live_status: Optional[str] = None  # "الشوط الأول", "الشوط الثاني", etc.
    is_live: bool = False

class MatchesResponse(BaseModel):
    total_matches: int
    matches: List[Match]
    scraped_at: str
    date_range: str

def parse_arabic_date(date_str: str) -> Optional[datetime]:
    """Parse Arabic date format to datetime"""
    try:
        # Extract date in format DD-MM-YYYY
        date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', date_str)
        if date_match:
            day, month, year = date_match.groups()
            return datetime(int(year), int(month), int(day))
    except Exception as e:
        print(f"Date parsing error: {e}")
    
    return None

def extract_match_info(match_elem, date_str: str, round_name: str) -> Optional[Match]:
    """Extract match information from HTML element"""
    try:
        # Extract match URL and ID
        match_url = match_elem.get('href', '')
        match_id = match_elem.get('match_id', '')
        
        # Extract team names
        home_name = match_elem.get('home_name', '')
        away_name = match_elem.get('away_name', '')
        
        # Extract team logos
        home_logo = match_elem.get('home_image', '')
        away_logo = match_elem.get('away_image', '')
        
        # Check if match is live
        is_live = 'live-match' in match_elem.get('class', [])
        
        # Initialize scores and status
        home_score = None
        away_score = None
        status = "upcoming"
        match_time = None
        live_minute = None
        live_status = None
        
        # Find result wrap for all match types
        result_wrap = match_elem.find('div', class_='result-wrap')
        
        if result_wrap:
            # Check for LIVE match - has active-match-progress div
            active_progress = match_elem.find('div', class_='active-match-progress')
            if active_progress or is_live:
                status = "live"
                
                # Extract live status (الشوط الأول, الشوط الثاني, etc.)
                live_status_elem = active_progress.find('span', class_='result-status-text') if active_progress else None
                if live_status_elem:
                    live_status = live_status_elem.text.strip()
                
                # Extract live minute
                minute_elem = active_progress.find('div', class_='number') if active_progress else None
                if minute_elem:
                    live_minute = minute_elem.text.strip()
                
                # Extract live scores from team result divs
                first_team_div = match_elem.find('div', class_='first-team')
                second_team_div = match_elem.find('div', class_='second-team')
                
                if first_team_div:
                    score_elem = first_team_div.find('div', class_='first-team-result')
                    if score_elem:
                        home_score = score_elem.text.strip()
                
                if second_team_div:
                    score_elem = second_team_div.find('div', class_='second-team-result')
                    if score_elem:
                        away_score = score_elem.text.strip()
            
            # Check for FINISHED match - has span with scores
            elif result_wrap.find('span', class_='first-team-result'):
                status = "finished"
                first_score = result_wrap.find('span', class_='first-team-result')
                second_score = result_wrap.find('span', class_='second-team-result')
                if first_score and second_score:
                    home_score = first_score.text.strip()
                    away_score = second_score.text.strip()
            
            # Check for UPCOMING match (has time)
            else:
                match_date_elem = result_wrap.find('b', class_='match-date')
                if match_date_elem:
                    match_time = match_date_elem.text.strip()
                    status = "upcoming"
        
        return Match(
            match_id=match_id,
            home_team=home_name,
            away_team=away_name,
            home_logo=home_logo,
            away_logo=away_logo,
            home_score=home_score,
            away_score=away_score,
            status=status,
            match_time=match_time,
            date=date_str,
            round=round_name,
            match_url=match_url if match_url.startswith('http') else f"https://www.ysscores.com{match_url}",
            live_minute=live_minute,
            live_status=live_status,
            is_live=is_live or status == "live"
        )
    
    except Exception as e:
        print(f"Error extracting match info: {e}")
        return None

def scrape_matches(html_content: str) -> List[Match]:
    """Scrape matches from HTML content"""
    soup = BeautifulSoup(html_content, 'html.parser')
    matches = []
    
    # Find the main match list container
    match_list_container = soup.find('div', id='match_list_conf')
    
    if not match_list_container:
        print("Could not find match_list_conf container")
        return matches
    
    # Find all sections (upcoming, live, finished, postponed)
    all_wrappers = match_list_container.find_all('div', class_='matches-wrapper', recursive=False)
    
    for wrapper in all_wrappers:
        # Get section title
        section_title_elem = wrapper.find('h3', class_='matches-top-title')
        section_type = section_title_elem.text.strip() if section_title_elem else "unknown"
        
        # Find the content div (coming_match_load, match_block_list, end_match_load, postponed_match_load)
        content_divs = wrapper.find_all(['div'], recursive=False)
        
        for content_div in content_divs:
            # Skip if it's just the title div
            if content_div.find('h3', class_='matches-top-title'):
                continue
            
            # Now find all children that are either week titles or match links
            children = content_div.find_all(recursive=False)
            
            current_round = ""
            current_date = ""
            
            for child in children:
                # Check if it's a round/date header
                if 'matches-week-title' in child.get('class', []):
                    round_elem = child.find('b')
                    date_elem = child.find('span', class_='date')
                    
                    if round_elem:
                        current_round = round_elem.text.strip()
                    if date_elem:
                        current_date = date_elem.text.strip()
                
                # Check if it's a match link
                elif child.name == 'a' and 'ajax-match-item' in child.get('class', []):
                    match_info = extract_match_info(child, current_date, current_round)
                    if match_info:
                        matches.append(match_info)
                
                # Check for nested matches wrapper (for live matches)
                elif 'matches-wrapper' in child.get('class', []):
                    nested_children = child.find_all(recursive=False)
                    for nested_child in nested_children:
                        if 'matches-week-title' in nested_child.get('class', []):
                            round_elem = nested_child.find('b')
                            date_elem = nested_child.find('span', class_='date')
                            
                            if round_elem:
                                current_round = round_elem.text.strip()
                            if date_elem:
                                current_date = date_elem.text.strip()
                        
                        elif nested_child.name == 'a' and 'ajax-match-item' in nested_child.get('class', []):
                            match_info = extract_match_info(nested_child, current_date, current_round)
                            if match_info:
                                matches.append(match_info)
    
    return matches

def filter_matches_by_date(matches: List[Match], target_date: datetime) -> List[Match]:
    """Filter matches starting from target_date (2 days ago) up to future"""
    filtered = []
    
    for match in matches:
        match_date = parse_arabic_date(match.date)
        if match_date and match_date >= target_date:
            filtered.append(match)
        # Include matches without valid dates (might be live or recent)
        elif not match_date and (match.status == "live" or match.status == "upcoming"):
            filtered.append(match)
    
    return filtered

@app.get("/", response_model=dict)
async def root():
    """API root endpoint"""
    return {
        "message": "Algerian Professional League Scraper API",
        "version": "1.0.0",
        "endpoints": {
            "/matches": "Get all matches from 2 days ago to future (max 8)",
            "/matches/all": "Get all available matches",
            "/health": "Health check"
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/matches", response_model=MatchesResponse)
async def get_matches():
    """
    Scrape matches from Algerian Professional League
    Returns matches from 2 days ago up to future (maximum 8 matches)
    """
    try:
        # Calculate target date (2 days ago)
        today = datetime.now()
        target_date = today - timedelta(days=2)
        
        # Fetch the page
        url = "https://www.ysscores.com/ar/championship/57146/Algerian-Professional-League-1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Scrape all matches
        all_matches = scrape_matches(response.text)
        
        # Filter matches from 2 days ago onwards
        filtered_matches = filter_matches_by_date(all_matches, target_date)
        
        # Sort matches: live first, then by date
        def sort_key(match):
            if match.status == "live":
                return (0, datetime.min)  # Live matches first
            match_date = parse_arabic_date(match.date)
            return (1, match_date if match_date else datetime.max)
        
        filtered_matches.sort(key=sort_key)
        
        # Limit to 8 matches
        limited_matches = filtered_matches[:8]
        
        return MatchesResponse(
            total_matches=len(limited_matches),
            matches=limited_matches,
            scraped_at=datetime.now().isoformat(),
            date_range=f"From {target_date.strftime('%Y-%m-%d')} onwards (max 8 matches)"
        )
    
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch data: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing data: {str(e)}")

@app.get("/matches/all", response_model=MatchesResponse)
async def get_all_matches():
    """
    Get all available matches without date filtering
    """
    try:
        url = "https://www.ysscores.com/ar/championship/57146/Algerian-Professional-League-1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Scrape all matches
        all_matches = scrape_matches(response.text)
        
        # Sort matches: live first, then by date
        def sort_key(match):
            if match.status == "live":
                return (0, datetime.min)
            match_date = parse_arabic_date(match.date)
            return (1, match_date if match_date else datetime.max)
        
        all_matches.sort(key=sort_key)
        
        return MatchesResponse(
            total_matches=len(all_matches),
            matches=all_matches,
            scraped_at=datetime.now().isoformat(),
            date_range="All available matches"
        )
    
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch data: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing data: {str(e)}")

# ============================================================================
# STATS ENDPOINT - Detailed Match Information
# ============================================================================

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
        event_data['time'] = time_element.get_text(strip=True).replace("'", '') if time_element else None

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
        if "'" in text:
            time_part, name_part = text.split("'", 1)
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
        if "'" in text:
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

def adjust_match_time(time_str):
    """تحويل وقت المباراة من صيغة 12 ساعة إلى 24 ساعة مع إضافة 8 ساعات"""
    try:
        time_str = time_str.strip()
        time_part = time_str.split()[0]
        period = time_str.split()[1] if len(time_str.split()) > 1 else ""
        
        hours, minutes = map(int, time_part.split(":"))
        
        if "مساءً" in period or "مساء" in period:
            if hours != 12:
                hours += 12
        elif "صباحاً" in period or "صباحا" in period:
            if hours == 12:
                hours = 0
        
        hours += 8
        
        if hours >= 24:
            hours -= 24
        
        return f"{hours:02d}:{minutes:02d}"
    
    except Exception:
        return time_str

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
                    
                    if title == "وقت المباراة":
                        content = adjust_match_time(content)
                    
                    match_info[title] = content
            break
    return match_info

# ------------------- استخراج معلومات المباراة -------------------

def format_match_time(raw_time):
    """دالة تنسيق الوقت: تضيف +8 ساعات وترجعه بصيغة 24h"""
    try:
        dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M")
        dt += timedelta(hours=8)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw_time

def extract_info(soup, teams_info):
    info = teams_info.copy()

    tag_status = soup.find("input", {"id": "match_status"})
    status_value = tag_status["value"] if tag_status and tag_status.has_attr("value") else None
    status_text = STATUS_MAP.get(status_value, "غير معروف")
    is_live = "False" if status_value in NOT_LIVE else "True"

    tag_time = soup.find("input", {"id": "match_time"})
    time_value = tag_time["value"] if tag_time and tag_time.has_attr("value") else None

    raw_time = compute_time_expr(status_value, time_value)
    match_time = format_match_time(raw_time) if raw_time else None

    info.update({
        "Time": match_time,
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

# ------------------- Main Stats Function -------------------

def get_match_data(match_id: str):
    base_url = "https://www.ysscores.com"
    match_page_url = f"{base_url}/ar/match/{match_id}/dummy"
    api_url = f"{base_url}/ar/get_match_detail?match_id={match_id}"

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # Step 1: Get team names and logos
    try:
        page_resp = requests.get(match_page_url, headers=headers, timeout=10)
        page_resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load match page: {e}")

    soup_page = BeautifulSoup(page_resp.text, "html.parser")
    teams = soup_page.select(".team-item")
    if len(teams) < 2:
        raise HTTPException(status_code=404, detail="Could not find team info on match page")

    def get_team_data(el):
        name_el = el.find("h3")
        name = name_el.get_text(strip=True) if name_el else el.find("img").get("title", "").strip()
        logo = el.find("img").get("src", "").strip()
        logo = change_logo_size(logo)
        return name, logo

    home_name, home_logo = get_team_data(teams[0])
    away_name, away_logo = get_team_data(teams[1])

    teams_info = {
        "HomeTeam": home_name,
        "HomeImgLink": home_logo,
        "AwayTeam": away_name,
        "AwayImgLink": away_logo
    }

    # Step 2: Get live match data
    try:
        api_resp = requests.get(api_url, headers=headers, timeout=10)
        api_resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load match API: {e}")

    # Step 3: Build full data
    try:
        return build_match_info(api_resp.text, teams_info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse match data: {e}")

# ------------------- Stats API Endpoint -------------------

@app.get("/stats/{match_id}")
async def get_match_stats(match_id: str):
    """
    Get detailed match statistics, events, and information
    
    Parameters:
    - match_id: The match ID from ysscores.com
    
    Example: /stats/4907637
    """
    try:
        match_data = get_match_data(match_id)
        return {
            "success": True,
            "match_id": match_id,
            "data": match_data,
            "scraped_at": datetime.now().isoformat()
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching match stats: {str(e)}")

import uvicorn
if __name__ == "__main__":
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

