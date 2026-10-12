"""
Shengya Weekly Award Alert
--------------------------
Every week this script searches Google News for newly awarded road and
construction contracts in Nigeria and neighbouring countries, picks out the
ones that are likely to need a concrete batching plant, and emails a digest.

No paid API needed. Settings come from GitHub Secrets:
  GMAIL_USER          the Gmail address that sends the email
  GMAIL_APP_PASSWORD  a Gmail "App Password" (not your normal password)
  ALERT_TO            where to send the digest (can be several, comma-separated)
"""
import csv, html, io, json, os, re, smtplib, ssl, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

SEEN_FILE = "seen.json"
DAYS = 7  # how far back to look

# (search words, country label, language settings)
EN_NG = ("en-NG", "NG", "NG:en")
EN_GH = ("en-GH", "GH", "GH:en")
FR_CM = ("fr", "CM", "CM:fr")
FR_BJ = ("fr", "BJ", "BJ:fr")
SEARCHES = [
    ('FEC approves road contract', "Nigeria", EN_NG),
    ('"contract" awarded road Messrs', "Nigeria", EN_NG),
    ('Umahi contract signing road', "Nigeria", EN_NG),
    ('rigid pavement road contract', "Nigeria", EN_NG),
    ('concrete road contract awarded state', "Nigeria", EN_NG),
    ('Lagos road contract awarded', "Nigeria", EN_NG),
    ('Rivers State road contract awarded', "Nigeria", EN_NG),
    ('Akwa Ibom road contract awarded', "Nigeria", EN_NG),
    ('Bayelsa road contract awarded', "Nigeria", EN_NG),
    ('Enugu road contract awarded', "Nigeria", EN_NG),
    ('FCTA Abuja road contract awarded', "Nigeria", EN_NG),
    ('estate housing project groundbreaking Nigeria', "Nigeria", EN_NG),
    ('Big Push road contract awarded', "Ghana", EN_GH),
    ('Ghana road contract awarded contractor', "Ghana", EN_GH),
    ('Cameroun marché route attribué', "Cameroon", FR_CM),
    ('Cameroun travaux routiers entreprise', "Cameroon", FR_CM),
    ('Bénin marché travaux routiers attribué', "Benin / Togo", FR_BJ),
    ('Togo travaux routiers entreprise', "Benin / Togo", FR_BJ),
    ('Abidjan-Lagos corridor contractor', "Regional", EN_NG),
]

# Words that make a story more likely to be a real buyer signal
STRONG = ["awarded", "award of contract", "messrs", "contract signing", "signed",
          "flag-off", "flag off", "flagged off", "mobilise", "mobilize",
          "attribué", "attribution", "adjudicataire", "lancement des travaux"]
CONCRETE = ["concrete", "rigid pavement", "crcp", "reinforced", "batching",
            "béton", "chaussée rigide"]
ROADISH = ["road", "highway", "bridge", "expressway", "dualisation", "dualization",
           "estate", "housing", "route", "autoroute", "pont", "chaussée", "airport", "port"]
NOISE = ["accident", "crash", "robbery", "kidnap", "killed", "protest", "football", "election"]

def fetch(query, lang):
    hl, gl, ceid = lang
    q = urllib.parse.quote(f"{query} when:{DAYS}d")
    url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def parse(xml_bytes):
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub("<[^>]+>", " ", it.findtext("description") or "")
        desc = html.unescape(re.sub(r"\s+", " ", desc)).strip()
        source = (it.findtext("source") or "").strip()
        pub = it.findtext("pubDate") or ""
        try:
            date = parsedate_to_datetime(pub).strftime("%d %b %Y")
        except Exception:
            date = ""
        items.append(dict(title=title, link=link, desc=desc, source=source, date=date))
    return items

def score(text):
    t = text.lower()
    if any(n in t for n in NOISE):
        return 0
    if not any(r in t for r in ROADISH):
        return 0
    s = 1
    s += 2 * sum(w in t for w in STRONG)
    s += 3 * sum(w in t for w in CONCRETE)
    return s

CONTRACTOR_PATTERNS = [
    r"Messrs\.?\s+([A-Z][A-Za-z0-9&.,'\- ]{2,70}(?:Limited|Ltd|Plc|PLC|Company|Nigeria|Construction|Engineering))",
    r"awarded to\s+([A-Z][A-Za-z0-9&.,'\- ]{2,70}(?:Limited|Ltd|Plc|Company|Nigeria|Construction|Engineering|Group))",
    r"attribué à (?:l'entreprise |la société )?([A-Z][\w&.,'\- ]{2,60})",
]
def find_contractor(text):
    for p in CONTRACTOR_PATTERNS:
        m = re.search(p, text)
        if m:
            return m.group(1).strip(" ,.")
    return ""

def priority(s):
    return "Hot" if s >= 6 else ("Warm" if s >= 3 else "Watch")

def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen)[-3000:], f)  # keep file small

def build_email(results):
    order = ["Nigeria", "Ghana", "Cameroon", "Benin / Togo", "Regional"]
    colours = {"Hot": "#C00000", "Warm": "#C55A11", "Watch": "#595959"}
    parts = [f"""<div style="font-family:Arial,sans-serif;max-width:720px">
<h2 style="color:#1F3864;margin-bottom:4px">Weekly Contract Award Alert</h2>
<p style="color:#595959;margin-top:0">{datetime.now(timezone.utc).strftime('%d %B %Y')} · {len(results)} new stories ·
Hot = award + concrete signals. A CSV for your lead tracker is attached.</p>"""]
    for country in order:
        rows = [r for r in results if r["country"] == country]
        if not rows:
            continue
        parts.append(f'<h3 style="color:#1F3864;border-bottom:2px solid #1F3864">{country} ({len(rows)})</h3>')
        for r in rows:
            c = f"<br><b>Possible contractor:</b> {html.escape(r['contractor'])}" if r["contractor"] else ""
            parts.append(f"""<p style="margin:10px 0">
<span style="color:{colours[r['priority']]};font-weight:bold">[{r['priority']}]</span>
<a href="{r['link']}">{html.escape(r['title'])}</a><br>
<span style="color:#595959;font-size:12px">{html.escape(r['source'])} · {r['date']}</span>{c}</p>""")
    parts.append("<p style='color:#595959;font-size:12px'>Next step: copy Hot items into the Lead Tracker and send the matching outreach script.</p></div>")
    return "".join(parts)

def build_csv(results):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Company", "Country", "Project / headline", "Award / news date", "Priority", "Source"])
    for r in results:
        w.writerow([r["contractor"] or "TBC", r["country"], r["title"], r["date"], r["priority"], r["link"]])
    return buf.getvalue().encode("utf-8")

def send(subject, html_body, csv_bytes):
    user = os.environ["GMAIL_USER"]
    pwd = os.environ["GMAIL_APP_PASSWORD"]
    to = [a.strip() for a in os.environ.get("ALERT_TO", user).split(",") if a.strip()]
    msg = MIMEMultipart()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.attach(MIMEText(html_body, "html"))
    if csv_bytes:
        att = MIMEApplication(csv_bytes, Name="new_leads.csv")
        att["Content-Disposition"] = 'attachment; filename="new_leads.csv"'
        msg.attach(att)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.sendmail(user, to, msg.as_string())

def main():
    seen = load_seen()
    found = {}
    for query, country, lang in SEARCHES:
        try:
            items = parse(fetch(query, lang))
        except Exception as e:
            print(f"Search failed: {query} -> {e}")
            continue
        for it in items:
            key = re.sub(r"\W+", "", it["title"].lower())[:90]
            if key in seen or key in found:
                continue
            s = score(it["title"] + " " + it["desc"])
            if s == 0:
                continue
            it.update(country=country, score=s, priority=priority(s),
                      contractor=find_contractor(it["title"] + " " + it["desc"]))
            found[key] = it
    results = sorted(found.values(), key=lambda r: -r["score"])[:60]
    print(f"{len(results)} new relevant stories")
    hot = sum(r["priority"] == "Hot" for r in results)
    if results:
        subject = f"Contract Award Alert: {len(results)} new ({hot} hot)"
        send(subject, build_email(results), build_csv(results))
    else:
        send("Contract Award Alert: nothing new this week",
             "<p style='font-family:Arial'>No new road/construction awards found this week.</p>", None)
    seen.update(found.keys())
    save_seen(seen)

if __name__ == "__main__":
    main()
