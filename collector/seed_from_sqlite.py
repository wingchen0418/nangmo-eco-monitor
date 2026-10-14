"""一次性脚本：从本地 SQLite 导出历史数据，预填 docs/data/history/ 与 latest.json，
让数字孪生页面部署后立刻有数据可看。仅本地运行一次，不进 GitHub Actions。"""
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = Path("/Users/czy/Downloads/课题/水质监测系统/data/water_quality.db")
DATA_DIR = BASE / "docs" / "data"
HIST_DIR = DATA_DIR / "history"
HIST_DIR.mkdir(parents=True, exist_ok=True)
CST = timezone(timedelta(hours=8))

con = sqlite3.connect(DB)
rows = con.execute("SELECT serial_no, create_time, device_name, payload FROM readings ORDER BY create_time").fetchall()

by_day = {}
latest_by_dev = {}
for serial, ts, name, payload in rows:
    day = datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d")
    by_day.setdefault(day, []).append({
        "serial_no": serial, "create_time": ts, "device_name": name,
        "payload": json.loads(payload),
    })
    if serial not in latest_by_dev or ts > latest_by_dev[serial]["create_time"]:
        latest_by_dev[serial] = {"create_time": ts, "payload": json.loads(payload)}

for day, items in by_day.items():
    (HIST_DIR / f"{day}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    print(f"{day}: {len(items)} 条")

# latest.json 快照（页面首次加载用）
devs = []
for serial, info in latest_by_dev.items():
    devs.append({"serialNo": serial, "categoryName": "", "remarks": serial,
                 "status": 1, "data": info["payload"]})
now = int(datetime.now(CST).timestamp())
latest = {"updated_at": datetime.fromtimestamp(now, CST).strftime("%Y-%m-%d %H:%M:%S"),
          "updated_ts": now, "devices": devs}
(DATA_DIR / "latest.json").write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
(DATA_DIR / "alerts.json").write_text("[]", encoding="utf-8")
(DATA_DIR / "alert_state.json").write_text("{}", encoding="utf-8")
print(f"latest.json: {len(devs)} 台设备快照")
