import json
import os
import subprocess
import time
import traceback
import requests

UA_STRING = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
FULL_HEADERS = {
    "User-Agent": UA_STRING,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.rolimons.com/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}
DEMAND_MAP = {0: "Terrible", 1: "Low", 2: "Normal", 3: "High", 4: "Amazing", None: "Unassigned"}
TAG_NAMES = {1: "Any", 2: "Demand", 3: "Rare", 4: "RAP", 5: "Wishlist",
             6: "Robux", 7: "Upgrade", 8: "Downgrade", 9: "Adds", 10: "Projecteds"}
TAG_ICONS = {1: "tradetagany", 2: "tradetagdemand", 3: "tradetagrares", 4: "tradetagrap",
             5: "tradetagwishlist", 6: "tradetagrobux", 7: "tradetagupgrade",
             8: "tradetagdowngrade", 9: "tradetagadds", 10: "tradetagprojecteds"}
ARCHIVE_FILE = "data/ad_archive.json"
ROBUX_TAG = 6
MIN_VALUE = 5000
MAX_VALUE = 50000
POLL_SECONDS = 30
PUSH_EVERY_N_CYCLES = 10
MAX_DURATION_SECONDS = 5 * 3600 + 50 * 60
MIN_CATALOG_BYTES = 400000
PRUNE_AFTER_HOURS = 24
DASHBOARD_MAX_ADS = 300

os.makedirs("data", exist_ok=True)
session = requests.Session()


def extract_js_object(text, start_marker="var item_details = {"):
    start = text.find(start_marker)
    if start == -1:
        raise ValueError("marker not found")
    start = start + len("var item_details = ")
    depth, in_string, escape, i = 0, False, False, start
    while i < len(text):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == chr(92):
                escape = True
            elif c == chr(34):
                in_string = False
        else:
            if c == chr(34):
                in_string = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
        i += 1
    raise ValueError("truncated")


def load_catalog(attempts=5):
    for n in range(1, attempts + 1):
        r = session.get("https://www.rolimons.com/catalog", headers=FULL_HEADERS, timeout=25)
        if r.status_code == 200 and len(r.text) >= MIN_CATALOG_BYTES and "var item_details = {" in r.text:
            raw = json.loads(extract_js_object(r.text))
            catalog = {}
            for v in raw.values():
                rap, value = v[8], v[16]
                catalog[v[-1]] = {
                    "name": v[0], "rap": rap, "value": value,
                    "conservative": min(rap, value) if value is not None else rap,
                    "demand": DEMAND_MAP.get(v[17], "Unassigned"),
                }
            print(f"catalog loaded: {len(catalog)} items")
            return catalog
        print(f"  catalog attempt {n}: {r.status_code}, {len(r.text):,} chars -- retrying")
        time.sleep(5 * n)
    raise RuntimeError("could not fetch a complete catalog page")


def price_items(catalog, item_ids):
    out = []
    for iid in item_ids:
        if iid in catalog:
            d = dict(catalog[iid])
            d["id"] = iid
            out.append(d)
    return out


def push_to_github(cycle_num):
    if cycle_num % PUSH_EVERY_N_CYCLES != 0:
        return
    try:
        subprocess.run(["git", "add", "data/ad_archive.json", "dashboard.html"], check=True, capture_output=True)
        result = subprocess.run(["git", "commit", "-m", f"scan cycle {cycle_num}"], capture_output=True, text=True)
        if "nothing to commit" in result.stdout + result.stderr:
            return
        subprocess.run(["git", "push"], check=True, capture_output=True, timeout=30)
        print("  pushed to GitHub")
    except Exception as e:
        print(f"  git push failed: {e}")

def run_once(catalog):
    r2 = session.get("https://api.rolimons.com/tradeads/v1/getrecentads",
                     headers=FULL_HEADERS, timeout=20)
    ads = r2.json()["trade_ads"]

    live = []
    for ad_id, ts, player_id, username, offer, request in ads:
        req_tags = request.get("tags", [])
        if ROBUX_TAG not in req_tags:
            continue
        offer_ids = offer.get("items", [])
        priced = price_items(catalog, offer_ids)
        if not priced:
            continue
        total_conservative = sum(p["conservative"] for p in priced)
        total_rap = sum(p["rap"] for p in priced)
        if not (MIN_VALUE <= total_conservative <= MAX_VALUE):
            continue

        request_item_ids = request.get("items", [])
        request_items = price_items(catalog, request_item_ids)
        ordered_tags = sorted(req_tags, key=lambda t: 0 if t == ROBUX_TAG else 1)
        tag_icons = [{"name": TAG_NAMES.get(t, f"tag{t}"),
                      "icon": f"https://www.rolimons.com/images/{TAG_ICONS.get(t,'tradetagany')}-420.png"}
                     for t in ordered_tags]

        live.append({
            "ad_id": str(ad_id), "player_id": player_id, "username": username,
            "offer_item_ids": offer_ids, "request_item_ids": request_item_ids,
            "items": priced, "total_value": total_conservative, "total_rap": total_rap,
            "offer_robux": offer.get("robux", 0),
            "request_tags": tag_icons, "request_robux": request.get("robux", 0),
            "request_items": request_items,
            "trade_url": f"https://www.roblox.com/Trade/TradeWindow.aspx?TradePartnerID={player_id}",
        })

    need_ids = set()
    for ad in live:
        need_ids.update(ad["offer_item_ids"])
        need_ids.update(ad["request_item_ids"])
    need_ids = sorted(need_ids)

    thumbs = {}
    for i in range(0, len(need_ids), 100):
        chunk = need_ids[i:i+100]
        try:
            tr = session.get("https://thumbnails.roblox.com/v1/assets",
                             params={"assetIds": ",".join(map(str, chunk)), "size": "150x150", "format": "Png"},
                             headers={"User-Agent": UA_STRING}, timeout=15)
            tr.raise_for_status()
            for entry in tr.json().get("data", []):
                if entry.get("state") == "Completed" and entry.get("imageUrl"):
                    thumbs[entry["targetId"]] = entry["imageUrl"]
        except Exception as e:
            print(f"thumbnail batch failed ({len(chunk)} ids): {e}")

    def attach_thumb(item):
        item = dict(item)
        item["thumb"] = thumbs.get(item["id"])
        return item

    for ad in live:
        ad["items"] = [attach_thumb(i) for i in ad["items"]]
        ad["request_items"] = [attach_thumb(i) for i in ad["request_items"]]

    archive = {}
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)

    now = time.time()
    cutoff = now - PRUNE_AFTER_HOURS * 3600
    before = len(archive)
    archive = {k: v for k, v in archive.items() if v.get("last_seen", 0) >= cutoff}
    pruned = before - len(archive)
    if pruned:
        print(f"  pruned {pruned} ads older than {PRUNE_AFTER_HOURS}h")
    live_ids = {ad["ad_id"] for ad in live}
    new_count = 0
    for ad in live:
        aid = ad["ad_id"]
        if aid in archive:
            ad["first_seen"] = archive[aid]["first_seen"]
            ad["is_new"] = False
        else:
            ad["first_seen"] = now
            ad["is_new"] = True
            new_count += 1
        ad["last_seen"] = now
        ad["active"] = True
        archive[aid] = ad

    for aid in archive:
        if aid not in live_ids:
            archive[aid]["active"] = False

    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f)

    active = sum(1 for a in archive.values() if a["active"])
    print(f"[{time.strftime('%H:%M:%S')}] archive: {len(archive)} total ({new_count} new, {active} active)")
    return archive

DASH = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trade Ad Watch</title>
<style>
body{background:#0d1017;color:#e6ebf4;font-family:-apple-system,Segoe UI,sans-serif;padding:20px;font-size:13px}
h1{font-size:22px;margin-bottom:2px;font-family:ui-monospace,Menlo,monospace}
.sub{color:#5d6884;font-size:12px;margin-bottom:16px;font-family:ui-monospace,Menlo,monospace}
.card{background:#1b2230;border:1px solid #252d3d;border-radius:6px;padding:14px;margin-bottom:12px}
.card.new{box-shadow:0 0 0 1px #3fd77c inset}
.card.gone{opacity:.45}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.user{font-weight:600;font-size:13px}
.badge{font-size:10px;background:#12331f;color:#3fd77c;padding:2px 8px;border-radius:99px;margin-left:6px}
.badge.gone{background:#2b2020;color:#8f6060}
.cols{display:flex;gap:16px}
.col{flex:1;min-width:0}
.collabel{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#5d6884;margin-bottom:6px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-bottom:8px}
.slot{aspect-ratio:1;background:#0d1017;border-radius:4px;overflow:hidden;position:relative}
.slot img{width:100%;height:100%;object-fit:contain}
.slot .tagname{position:absolute;bottom:2px;left:2px;right:2px;font-size:8px;text-align:center;
  color:#fff;text-shadow:0 0 3px #000;font-weight:700;text-transform:uppercase}
.stats{font-size:11px;line-height:1.7}
.stats .val{color:#f0a830}
.stats .rap{color:#3fd77c}
.stats .rbx{color:#4db8ff}
a.trade{display:block;text-align:center;margin-top:10px;color:#4db8ff;text-decoration:none;
  border:1px solid #36415a;padding:7px;border-radius:4px;font-size:12px}
a.trade:hover{background:#252d3d}
</style></head><body>
<h1>Trade Ad Watch</h1><div class="sub" id="stamp"></div><div id="cards"></div>
<script>
const DATA = __DATA_JSON__;
document.getElementById('stamp').textContent =
  new Date(DATA.generated_at*1000).toLocaleString()+'  --  '+DATA.count+' total, '+DATA.active_count+' live';
function slot(i){return i.thumb?`<div class="slot"><img src="${i.thumb}" title="${i.name}"></div>`:'<div class="slot"></div>'}
function tag(t){return `<div class="slot"><img src="${t.icon}" title="${t.name}"><div class="tagname">${t.name}</div></div>`}
function pad(a,n){const o=a.slice(0,n);while(o.length<n)o.push('<div class="slot"></div>');return o.join('')}
document.getElementById('cards').innerHTML=DATA.ads.map(a=>{
  const b=a.is_new?'<span class="badge">NEW</span>':(!a.active?'<span class="badge gone">GONE</span>':'');
  return `<div class="card ${a.is_new?'new':''} ${!a.active?'gone':''}">
    <div class="head"><span class="user">${a.username}${b}</span></div>
    <div class="cols">
      <div class="col"><div class="collabel">Offering</div>
        <div class="grid">${pad(a.items.map(slot),4)}</div>
        <div class="stats"><div class="val">Value ${a.total_value.toLocaleString()}</div>
        <div class="rap">RAP ${a.total_rap.toLocaleString()}</div>
        ${a.offer_robux>0?`<div class="rbx">+${a.offer_robux.toLocaleString()} Robux</div>`:''}</div></div>
      <div class="col"><div class="collabel">Requesting</div>
        <div class="grid">${pad(a.request_items.map(slot).concat(a.request_tags.map(tag)),4)}</div>
        <div class="stats">${a.request_robux>0?`<div class="rbx">Asking ${a.request_robux.toLocaleString()}</div>`:'<div class="rbx">Robux accepted</div>'}</div></div>
    </div>
    <a class="trade" href="${a.trade_url}" target="_blank">Open trade window</a></div>`;
}).join('');
</script></body></html>"""


def write_dashboard(archive):
    all_ads = sorted(archive.values(), key=lambda a: a["last_seen"], reverse=True)
    shown = all_ads[:DASHBOARD_MAX_ADS]
    out = {"generated_at": time.time(), "count": len(all_ads),
           "active_count": sum(1 for a in all_ads if a["active"]), "ads": shown}
    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(DASH.replace("__DATA_JSON__", json.dumps(out)))


if __name__ == "__main__":
    catalog = load_catalog()
    start = time.time()
    print(f"scanning for up to {MAX_DURATION_SECONDS/3600:.1f}h, polling every {POLL_SECONDS}s")
    cycle = 0
    while time.time() - start < MAX_DURATION_SECONDS:
        try:
            archive = run_once(catalog)
            write_dashboard(archive)
            push_to_github(cycle)
        except Exception:
            print("cycle failed, retrying next interval:")
            traceback.print_exc()
        cycle += 1
        time.sleep(POLL_SECONDS)
    print("time budget reached -- exiting cleanly")
