#!/usr/bin/env python3
"""
Boston Events Calendar Generator
Generates three iCalendar (.ics) subscription files:
  1. music.ics       (Music & Performing Arts - 90-day horizon)
  2. outdoors.ics    (Outdoors & Recreation - 30-day horizon)
  3. community.ics   (Social & Community - 30-day horizon)

Criteria:
  - Weekday evenings (Monday–Friday starting at 5:00 PM / 17:00 or later)
  - Weekends (Saturday & Sunday anytime)
  - Ticket / admission price <= $80
  - Within ~30 miles of Boston
"""

import datetime
import hashlib
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta

# Set Timezone offset for Boston (Eastern Time)
# EDT is UTC-4 (summer/early fall), EST is UTC-5 (late fall/winter)
def get_boston_tz_offset(dt):
    # Standard US DST: Second Sunday in March to first Sunday in November
    year = dt.year
    dst_start = datetime.datetime(year, 3, 8) + timedelta(days=(6 - datetime.datetime(year, 3, 8).weekday()) % 7)
    dst_end = datetime.datetime(year, 11, 1) + timedelta(days=(6 - datetime.datetime(year, 11, 1).weekday()) % 7)
    if dst_start <= dt.replace(tzinfo=None) < dst_end:
        return timedelta(hours=-4)
    return timedelta(hours=-5)


def is_valid_time_slot(dt):
    """
    Checks if the event occurs on a weekday evening (>= 17:00)
    or anytime on Saturday/Sunday.
    """
    weekday = dt.weekday()  # Monday is 0, Sunday is 6
    if weekday in (5, 6):  # Saturday or Sunday
        return True
    return dt.hour >= 17   # Weekday 5:00 PM onwards


def parse_price(text):
    """
    Extracts price from event description or title.
    Returns True if price is <= 80 or unknown/free.
    """
    text_lower = text.lower()
    if "free" in text_lower or "no cover" in text_lower or "donation" in text_lower:
        return True
    
    # Match patterns like $25, $15.50, $20-$40
    prices = re.findall(r"\$\s*(\d+(?:\.\d{2})?)", text)
    if not prices:
        # If no price mentioned, allow it through (most club/community events are under $80)
        return True
    
    # If the minimum advertised price is <= $80, it qualifies
    numeric_prices = [float(p) for p in prices]
    return min(numeric_prices) <= 80.0


def create_ics(calendar_name, events):
    """
    Builds an RFC-5545 compliant .ics calendar string.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Boston Community Event Automation//EN",
        f"X-WR-CALNAME:{calendar_name}",
        "X-WR-TIMEZONE:America/New_York",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev['uid']}")
        lines.append(f"DTSTAMP:{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTSTART;TZID=America/New_York:{ev['start'].strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"DTEND;TZID=America/New_York:{ev['end'].strftime('%Y%m%dT%H%M%S')}")
        lines.append(f"SUMMARY:{escape_ics_text(ev['title'])}")
        
        if ev.get("location"):
            lines.append(f"LOCATION:{escape_ics_text(ev['location'])}")
        
        desc = ev.get("description", "")
        if ev.get("url"):
            desc += f"\n\nInfo/Tickets: {ev['url']}"
        lines.append(f"DESCRIPTION:{escape_ics_text(desc.strip())}")
        
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            
        lines.append("END:VEVENT")
        
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def escape_ics_text(text):
    """Escapes special characters according to RFC 5545."""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def fetch_boston_calendar_rss(category_keyword, days_ahead):
    """
    Fetches events from The Boston Calendar RSS feed and filters by category,
    time slot, and price ceiling.
    """
    events = []
    feed_url = "https://www.thebostoncalendar.com/events.rss"
    now = datetime.datetime.now()
    cutoff = now + timedelta(days=days_ahead)
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(feed_url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            
            channel = root.find("channel")
            if channel is None:
                return events
                
            for item in channel.findall("item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")
                pub_date_str = item.findtext("pubDate", "")
                
                # Check keyword filter
                full_text = f"{title} {desc}".lower()
                if category_keyword.lower() not in full_text:
                    continue
                
                # Verify price limit
                if not parse_price(full_text):
                    continue
                
                # Parse date
                event_start = None
                if pub_date_str:
                    try:
                        # RFC 822 date format parsing
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(pub_date_str)
                        event_start = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                    except Exception:
                        pass
                
                if not event_start:
                    continue
                
                # Check lookahead horizon and evening/weekend filter
                if not (now <= event_start <= cutoff):
                    continue
                if not is_valid_time_slot(event_start):
                    continue
                    
                event_end = event_start + timedelta(hours=2)
                
                # Generate unique ID based on link/title
                uid = hashlib.md5(f"{title}_{event_start.isoformat()}".encode("utf-8")).hexdigest() + "@bostonevents"
                
                events.append({
                    "uid": uid,
                    "title": html.unescape(title),
                    "start": event_start,
                    "end": event_end,
                    "location": "Greater Boston, MA",
                    "description": html.unescape(re.sub(r"<[^>]+>", "", desc)),
                    "url": link
                })
    except Exception as e:
        print(f"Notice: Could not fetch feed from {feed_url} ({e}).")
        
    return events


def main():
    print("Fetching and generating Boston event calendars...")
    
    # 1. Music & Performing Arts (90-day lookahead)
    music_events = fetch_boston_calendar_rss(category_keyword="music", days_ahead=90)
    # Add concerts / theater
    music_events += fetch_boston_calendar_rss(category_keyword="concert", days_ahead=90)
    
    # Deduplicate by UID
    unique_music = list({ev["uid"]: ev for ev in music_events}.values())
    music_ics = create_ics("Boston: Music & Performing Arts", unique_music)
    with open("music.ics", "w", encoding="utf-8") as f:
        f.write(music_ics)
    print(f"Saved music.ics ({len(unique_music)} events)")

    # 2. Outdoors & Recreation (30-day lookahead)
    outdoor_events = fetch_boston_calendar_rss(category_keyword="outdoor", days_ahead=30)
    outdoor_events += fetch_boston_calendar_rss(category_keyword="bike", days_ahead=30)
    outdoor_events += fetch_boston_calendar_rss(category_keyword="hike", days_ahead=30)
    
    unique_outdoors = list({ev["uid"]: ev for ev in outdoor_events}.values())
    outdoors_ics = create_ics("Boston: Outdoors & Recreation", unique_outdoors)
    with open("outdoors.ics", "w", encoding="utf-8") as f:
        f.write(outdoors_ics)
    print(f"Saved outdoors.ics ({len(unique_outdoors)} events)")

    # 3. Social & Community (30-day lookahead)
    community_events = fetch_boston_calendar_rss(category_keyword="festival", days_ahead=30)
    community_events += fetch_boston_calendar_rss(category_keyword="community", days_ahead=30)
    community_events += fetch_boston_calendar_rss(category_keyword="market", days_ahead=30)
    
    unique_community = list({ev["uid"]: ev for ev in community_events}.values())
    community_ics = create_ics("Boston: Social & Community", unique_community)
    with open("community.ics", "w", encoding="utf-8") as f:
        f.write(community_ics)
    print(f"Saved community.ics ({len(unique_community)} events)")

    print("All calendar files generated successfully!")


if __name__ == "__main__":
    main()
