import json
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
PLAYER_ID = 284716792
ARCHIVE_FILE = "data/ad_archive.json"
NEVER_TOUCH = set()


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


print("fetching catalog...")
r = requests.get("https://www.rolimons.com/catalog", headers=UA, timeout=20)
raw = json.loads(extract_js_object(r.text))
catalog = {}
for v in raw.values():
    item_id = v[-1]
    catalog[item_id] = {"name": v[0], "rap": v[8], "value": v[16], "projected": None}
print(f"catalog loaded: {len(catalog)} items")

print("fetching projected-status flags...")
try:
    rp = requests.get("https://www.rolimons.com/itemapi/itemdetails", headers=UA, timeout=20)
    proj_data = rp.json().get("items", {})
    flagged = 0
    for item_id_str, fields in proj_data.items():
        item_id = int(item_id_str)
        if item_id in catalog and len(fields) > 7:
            is_projected = fields[7] == 1
            catalog[item_id]["projected"] = is_projected
            if is_projected:
                flagged += 1
    print(f"projected status known for {len(proj_data)} items ({flagged} currently projected)")
except Exception as e:
    print(f"projected-status fetch failed, proceeding without it: {e}")

print("fetching your inventory...")
r2 = requests.get(f"https://api.rolimons.com/players/v1/playerassets/{PLAYER_ID}", headers=UA, timeout=20)
inv_data = r2.json()

held_asset_ids = set()
for h in inv_data.get("holds", []):
    held_asset_ids.add(h if isinstance(h, int) else h.get("assetId"))

your_items = {}
for item_id_str, asset_instance_ids in inv_data["playerAssets"].items():
    item_id = int(item_id_str)
    if item_id in NEVER_TOUCH:
        continue
    free_qty = len(asset_instance_ids) - sum(1 for a in asset_instance_ids if a in held_asset_ids)
    if free_qty <= 0:
        continue
    info = catalog.get(item_id)
    if not info:
        continue
    hi = max(info["rap"], info["value"] or info["rap"])
    your_items[item_id] = {"name": info["name"], "rap": info["rap"], "value": info["value"], "hi": hi, "copies": free_qty}
print(f"{len(your_items)} of your item types eligible")

with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
    archive = json.load(f)
active_ads = [a for a in archive.values() if a.get("active")]
print(f"{len(active_ads)} active ads in archive")

skipped_projected = 0
safe_ads = []
for ad in active_ads:
    is_risky = any(catalog.get(iid, {}).get("projected") is True for iid in ad["offer_item_ids"])
    if is_risky:
        skipped_projected += 1
        continue
    safe_ads.append(ad)
print(f"{skipped_projected} ads skipped for offering a confirmed-projected item")
print(f"{len(safe_ads)} ads remain\n")

need_ids = set(your_items.keys())
for ad in safe_ads:
    need_ids.update(ad["offer_item_ids"])
need_ids = sorted(need_ids)

thumbs = {}
for i in range(0, len(need_ids), 100):
    chunk = need_ids[i:i+100]
    try:
        tr = requests.get("https://thumbnails.roblox.com/v1/assets",
                           params={"assetIds": ",".join(map(str, chunk)), "size": "150x150", "format": "Png"},
                           headers=UA, timeout=15)
        tr.raise_for_status()
        for entry in tr.json().get("data", []):
            if entry.get("state") == "Completed" and entry.get("imageUrl"):
                thumbs[entry["targetId"]] = entry["imageUrl"]
    except Exception as e:
        print(f"thumbnail batch failed: {e}")

your_items_out = {str(iid): {**info, "id": iid, "thumb": thumbs.get(iid)} for iid, info in your_items.items()}
ads_out = []
for ad in safe_ads:
    items = [{**i, "thumb": thumbs.get(i["id"])} for i in ad["items"]]
    total_rap = sum(i["rap"] for i in items)
    ads_out.append({
        "ad_id": ad["ad_id"], "username": ad["username"], "items": items,
        "total_value": ad["total_value"], "total_rap": total_rap, "trade_url": ad["trade_url"],
    })

out = {"your_items": your_items_out, "ads": ads_out}
print(f"baking {len(ads_out)} ads and {len(your_items_out)} of your item types into the page")

html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Offer Composer</title>
<style>
body{background:#0d1017;color:#e6ebf4;font-family:-apple-system,Segoe UI,sans-serif;padding:20px;font-size:13px}
h1{font-size:22px;margin-bottom:2px;font-family:ui-monospace,Menlo,monospace}
.sub{color:#5d6884;font-size:12px;margin-bottom:10px;font-family:ui-monospace,Menlo,monospace}
.warn{background:#241a08;border:1px solid #3a2810;color:#f0a830;padding:10px 14px;border-radius:5px;
  font-size:12px;margin-bottom:16px}
.settings{background:#1b2230;border:1px solid #252d3d;border-radius:6px;padding:14px;margin-bottom:16px;
  display:flex;gap:24px;flex-wrap:wrap;align-items:flex-end}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#5d6884}
.field input[type=number]{width:70px;background:#0d1017;border:1px solid #36415a;color:#e6ebf4;
  padding:6px 8px;border-radius:4px;font-family:ui-monospace,Menlo,monospace}
.card{background:#1b2230;border:1px solid #252d3d;border-left:3px solid #f0a830;border-radius:6px;
  padding:14px;margin-bottom:12px}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.user{font-weight:600;font-size:13px}
.cols{display:flex;gap:16px}
.col{flex:1;min-width:0}
.collabel{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:#5d6884;margin-bottom:6px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-bottom:8px}
.grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin-bottom:8px}
.itemcell{background:#0d1017;border-radius:4px;overflow:hidden;padding-bottom:4px}
.itemcell .thumb{aspect-ratio:1;width:100%}
.itemcell .thumb img{width:100%;height:100%;object-fit:contain}
.itemcell .itxt{padding:2px 4px;font-size:9px;line-height:1.4}
.itemcell .iname{color:#c5cddb;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.itemcell .irap{color:#3fd77c}
.itemcell .ival{color:#f0a830}
.robuxcell{background:#0d2233;border:1px solid #1a4a6e;border-radius:4px;overflow:hidden;padding-bottom:4px}
.robuxcell .thumb{aspect-ratio:1;width:100%;display:flex;align-items:center;justify-content:center;
  color:#4db8ff;font-size:22px;font-weight:700}
.robuxcell .itxt{padding:2px 4px;font-size:9px;line-height:1.4;text-align:center}
.robuxcell .iname{color:#4db8ff;font-weight:700}
.robuxcell .irap{color:#4db8ff;font-family:ui-monospace,Menlo,monospace;font-size:10px}
.sidetotal{display:flex;gap:14px;font-size:11px;color:#8f9bb3;margin-bottom:10px;padding-top:2px;
  border-top:1px solid #252d3d}
.sidetotal b{color:#e6ebf4;font-family:ui-monospace,Menlo,monospace}
.sidetotal .note{color:#5d6884;font-size:9px;display:block}
.metrics{display:flex;gap:16px;margin-top:8px;font-size:12px;align-items:center}
.metrics b{font-family:ui-monospace,Menlo,monospace;font-size:16px;color:#3fd77c}
.btnrow{display:flex;gap:8px;margin-top:10px}
a.trade,button.copy{flex:1;text-align:center;color:#4db8ff;text-decoration:none;
  border:1px solid #36415a;padding:7px;border-radius:4px;font-size:12px;background:none;
  font-family:inherit;cursor:pointer}
a.trade:hover,button.copy:hover{background:#252d3d}
button.copy.copied{color:#3fd77c;border-color:#3fd77c}
.empty{color:#5d6884;text-align:center;padding:30px 0}
</style></head>
<body>
<h1>Offer Composer</h1>
<div class="sub" id="stamp"></div>
<div class="warn">These offers each assume your <b>full</b> inventory is available -- they don't reserve items
against each other. Sending one trade may make others below that use the same item(s) no longer possible.
Ads offering a known-projected item are excluded entirely. ROI already accounts for eventually selling the
received item and paying the 30% marketplace fee. Re-run after sending real trades for a fresh list.</div>

<div class="settings">
  <div class="field"><label>Target ROI %</label><input type="number" id="targetRoi" value="10" min="0" max="200" step="1"></div>
  <div class="field"><label>Min ROI %</label><input type="number" id="minRoi" value="3" min="-50" max="200" step="1"></div>
  <div class="field"><label>Max ROI %</label><input type="number" id="maxRoi" value="20" min="0" max="500" step="1"></div>
  <div class="field"><label>Robux ratio %</label><input type="number" id="robuxRatio" value="15" min="0" max="50" step="1"></div>
  <div class="field"><label>Prefer fewer items</label><input type="number" id="fewerItemsPenalty" value="5" min="0" max="30" step="1"></div>
  <div class="field"><label>Max offers shown</label><input type="number" id="maxOffers" value="30" min="1" max="200" step="1"></div>
</div>

<div id="cards"></div>

<script>
const DATA = __DATA_JSON__;

function findBestOffer(remaining, B, robuxRatio, targetRoi, minRoi, maxRoi, itemCountPenalty) {
  const poolIds = Object.keys(remaining).slice(0, 26);
  let best = null;
  function evaluate(comboIds) {
    const A = comboIds.reduce((s, id) => s + remaining[id].hi, 0);
    if (A === 0) return;
    const R = Math.round(robuxRatio * A);
    const deployed = 0.7 * A + R;
    const profit = 0.7 * B - deployed;
    const roi = deployed > 0 ? profit / deployed : -Infinity;
    if (roi > maxRoi || roi < minRoi) return;
    const dist = Math.abs(roi - targetRoi) + (comboIds.length - 1) * itemCountPenalty;
    if (best === null || dist < best.dist) best = { comboIds: [...comboIds], A, R, profit, roi, dist };
  }
  function dfs(start, cur) {
    if (cur.length) evaluate(cur);
    if (cur.length >= 4) return;
    for (let i = start; i < poolIds.length; i++) { cur.push(poolIds[i]); dfs(i + 1, cur); cur.pop(); }
  }
  dfs(0, []);
  return best;
}

function fmt(n) { return n === null || n === undefined ? '-' : n.toLocaleString(); }

function itemCell(i) {
  return `<div class="itemcell">
    <div class="thumb">${i.thumb ? `<img src="${i.thumb}" title="${i.name}">` : ''}</div>
    <div class="itxt">
      <div class="iname">${i.name}</div>
      <div class="irap">RAP ${fmt(i.rap)}</div>
      <div class="ival">Val ${fmt(i.value)}</div>
    </div>
  </div>`;
}
function robuxCell(amount) {
  return `<div class="robuxcell">
    <div class="thumb">R$</div>
    <div class="itxt">
      <div class="iname">Robux</div>
      <div class="irap">${amount.toLocaleString()}</div>
    </div>
  </div>`;
}
function pad(slots, n, cls) {
  const out = slots.slice(0, n);
  while (out.length < n) out.push(`<div class="${cls}"></div>`);
  return out.join('');
}

function copyList(btn, names, robux) {
  const lines = names.map(n => '- ' + n);
  if (robux > 0) lines.push('- ' + robux.toLocaleString() + ' Robux');
  navigator.clipboard.writeText(lines.join('\\n')).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy list'; btn.classList.remove('copied'); }, 1500);
  });
}

function recompute() {
  const targetRoi = parseFloat(document.getElementById('targetRoi').value) / 100;
  const minRoi = parseFloat(document.getElementById('minRoi').value) / 100;
  const maxRoi = parseFloat(document.getElementById('maxRoi').value) / 100;
  const robuxRatio = parseFloat(document.getElementById('robuxRatio').value) / 100;
  const fewerItemsPenalty = parseFloat(document.getElementById('fewerItemsPenalty').value) / 100;
  const maxOffers = parseInt(document.getElementById('maxOffers').value) || 30;

  const results = [];
  for (const ad of DATA.ads) {
    const best = findBestOffer(DATA.your_items, ad.total_value, robuxRatio, targetRoi, minRoi, maxRoi, fewerItemsPenalty);
    if (!best) continue;
    results.push({ ad, best });
  }
  results.sort((a, b) => a.best.dist - b.best.dist);
  const shown = results.slice(0, maxOffers);

  document.getElementById('stamp').textContent =
    shown.length + ' offers shown out of ' + results.length + ' viable (' + DATA.ads.length + ' active, non-projected ads)';

  document.getElementById('cards').innerHTML = shown.length ? shown.map(({ad, best}) => {
    const items = best.comboIds.map(id => DATA.your_items[id]);
    const yourValTotal = items.reduce((s, i) => s + (i.value !== null ? i.value : i.rap), 0) + best.R;
    const yourRapTotal = items.reduce((s, i) => s + i.rap, 0) + best.R;
    const yourCells = items.map(itemCell);
    if (best.R > 0) yourCells.push(robuxCell(best.R));
    const names = items.map(i => i.name);

    return `<div class="card">
      <div class="head"><span class="user">${ad.username}</span></div>
      <div class="cols">
        <div class="col">
          <div class="collabel">They're Offering</div>
          <div class="grid4">${pad(ad.items.map(itemCell), 4, 'itemcell')}</div>
          <div class="sidetotal">
            <span>Total RAP <b>${fmt(ad.total_rap)}</b></span>
            <span>Total (Conservative) <b>${fmt(ad.total_value)}</b><span class="note">used for profit calc</span></span>
          </div>
        </div>
        <div class="col">
          <div class="collabel">Your Offer</div>
          <div class="grid5">${pad(yourCells, 5, 'itemcell')}</div>
          <div class="sidetotal">
            <span>Total RAP <b>${fmt(yourRapTotal)}</b></span>
            <span>Total Value <b>${fmt(yourValTotal)}</b></span>
          </div>
        </div>
      </div>
      <div class="metrics">
        <div>Profit<br><b>+${Math.round(best.profit).toLocaleString()}</b></div>
        <div>ROI<br><b>${(best.roi*100).toFixed(1)}%</b></div>
      </div>
      <div class="btnrow">
        <a class="trade" href="${ad.trade_url}" target="_blank">Open trade window</a>
        <button class="copy" onclick='copyList(this, ${JSON.stringify(names)}, ${best.R})'>Copy list</button>
      </div>
    </div>`;
  }).join('') : '<div class="empty">Nothing clears these settings.</div>';
}

['targetRoi', 'minRoi', 'maxRoi', 'robuxRatio', 'fewerItemsPenalty', 'maxOffers'].forEach(id =>
  document.getElementById(id).addEventListener('input', recompute));
recompute();
</script>
</body></html>"""

html = html.replace("__DATA_JSON__", json.dumps(out))
with open("dashboard.html", "w", encoding="utf-8") as f:
    f.write(html)
print("wrote dashboard.html")
