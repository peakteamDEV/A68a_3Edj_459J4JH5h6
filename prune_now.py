import json, time, os

with open("data/ad_archive.json", encoding="utf-8") as f:
    archive = json.load(f)

before = len(archive)
cutoff = time.time() - 24 * 3600
archive = {k: v for k, v in archive.items() if v.get("last_seen", 0) >= cutoff}

with open("data/ad_archive.json", "w", encoding="utf-8") as f:
    json.dump(archive, f)

print(f"archive: {before} -> {len(archive)} ads")
print(f"archive file now {os.path.getsize('data/ad_archive.json')/1e6:.2f} MB")
