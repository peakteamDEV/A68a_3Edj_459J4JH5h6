import re

src = open("scan.py", encoding="utf-8").read()

# 1. add retention settings
src = src.replace(
    'MIN_CATALOG_BYTES = 400000',
    'MIN_CATALOG_BYTES = 400000\nPRUNE_AFTER_HOURS = 24\nDASHBOARD_MAX_ADS = 300'
)

# 2. prune stale ads right after the archive is loaded
old_load = '''    archive = {}
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)

    now = time.time()'''
new_load = '''    archive = {}
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            archive = json.load(f)

    now = time.time()
    cutoff = now - PRUNE_AFTER_HOURS * 3600
    before = len(archive)
    archive = {k: v for k, v in archive.items() if v.get("last_seen", 0) >= cutoff}
    pruned = before - len(archive)
    if pruned:
        print(f"  pruned {pruned} ads older than {PRUNE_AFTER_HOURS}h")'''
assert old_load in src, "archive-load block not found"
src = src.replace(old_load, new_load)

# 3. cap how many ads the dashboard renders
old_dash = '''    all_ads = sorted(archive.values(), key=lambda a: a["last_seen"], reverse=True)
    out = {"generated_at": time.time(), "count": len(all_ads),
           "active_count": sum(1 for a in all_ads if a["active"]), "ads": all_ads}'''
new_dash = '''    all_ads = sorted(archive.values(), key=lambda a: a["last_seen"], reverse=True)
    shown = all_ads[:DASHBOARD_MAX_ADS]
    out = {"generated_at": time.time(), "count": len(all_ads),
           "active_count": sum(1 for a in all_ads if a["active"]), "ads": shown}'''
assert old_dash in src, "dashboard block not found"
src = src.replace(old_dash, new_dash)

open("scan.py", "w", encoding="utf-8").write(src)
print("scan.py patched: 24h retention + dashboard capped at 300 ads")
