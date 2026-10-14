"""
GitHub Actions 采集脚本：聚天然平台 → JSON 数据文件 → 微信预警
零依赖（仅用标准库），由 .github/workflows/collect.yml 定时驱动。
数据写入 docs/data/，由 GitHub Pages 直接托管供数字孪生页面读取。
"""
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------- 配置（从 GitHub Secrets 注入） ----------
JTR_TOKEN = os.environ.get("JTR_TOKEN", "")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")
DISABLED_RULES = {"R3"}  # pH探头故障已知，后续ESP32自测替代

API = "https://iot.jutianran.cn/index.php"
SCHOOL_ID = 10089
DETAIL_DEVICES = [
    {"deviceId": 1157, "serialNo": "ZJ_SZ06_009", "name": "水质-深水区"},
    {"deviceId": 1187, "serialNo": "ZJ_SZ05_010", "name": "水质-实验室水生生态"},
    {"deviceId": 1154, "serialNo": "QXZ-A0014", "name": "气象监测站"},
    {"deviceId": 1155, "serialNo": "ZJ_KQ04_002", "name": "空气质量监测站"},
]

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
HISTORY_DIR = DATA_DIR / "history"
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

CST = timezone(timedelta(hours=8))
NOW = int(time.time())


def api_get(route, params=""):
    """路由格式 school.device/list（模块名的点保留，最后一个点变斜杠）"""
    i = route.rindex(".")
    r = route[:i] + "%2F" + route[i + 1:]
    url = f"{API}?s=%2Fapi%2F{r}&token={JTR_TOKEN}&schoolId={SCHOOL_ID}{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        j = json.loads(resp.read())
    if j.get("code") != 1:
        raise RuntimeError(f"API错误: {j.get('msg')}")
    return j["data"]


def push(text):
    """双通道推送：企业微信机器人 + PushPlus"""
    ok = True
    if WECOM_WEBHOOK:
        try:
            req = urllib.request.Request(
                WECOM_WEBHOOK,
                data=json.dumps({"msgtype": "text", "text": {"content": text}}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            ok = False
            print("企业微信推送失败:", e)
    if PUSHPLUS_TOKEN:
        try:
            req = urllib.request.Request(
                "https://www.pushplus.plus/send",
                data=json.dumps({"token": PUSHPLUS_TOKEN,
                                 "title": text.split("\n")[0],
                                 "content": text, "template": "txt"}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            ok = False
            print("PushPlus推送失败:", e)
    return ok


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


# ---------- 预警规则引擎 v0.1（9月数据回测标定 + 2小时防抖） ----------
alert_state = load_json(DATA_DIR / "alert_state.json", {})
alerts_log = load_json(DATA_DIR / "alerts.json", [])


def raise_alert(rule, level, serial_no, start_ts, end_ts, detail):
    if any(rule.startswith(d) for d in DISABLED_RULES):
        return
    key = f"{rule}|{level}|{serial_no}"
    if alert_state.get(key, 0) > end_ts - 7200:  # 2小时防抖
        return
    alert_state[key] = end_ts
    alerts_log.insert(0, {
        "rule": rule, "level": level, "serial_no": serial_no,
        "start_ts": start_ts, "end_ts": end_ts, "detail": detail,
        "time": datetime.fromtimestamp(end_ts, CST).strftime("%Y-%m-%d %H:%M"),
    })
    del alerts_log[100:]
    icon = "🔴" if level == "红" else "🟡"
    t = datetime.fromtimestamp(end_ts, CST).strftime("%Y-%m-%d %H:%M")
    push(f"{icon}【校园水生态预警】{rule}\n级别: {level}\n设备: {serial_no}\n时间: {t}\n{detail}")


def check_alerts(serial_no, rows):
    """rows: 按时间升序"""
    if not rows:
        return
    last = rows[-1]

    # R4 数据离线
    if NOW - int(last["create_time"]) > 900:
        raise_alert("R4-数据离线", "黄", serial_no, int(last["create_time"]), NOW,
                    f"最新数据时间 {datetime.fromtimestamp(int(last['create_time']), CST).strftime('%m-%d %H:%M')}，已超15分钟未更新")

    # R1 DO低氧
    do_val = last.get("do")
    if do_val is not None and do_val != "":
        try:
            do_val = float(do_val)
            if do_val < 2:
                raise_alert("R1-DO严重低氧", "红", serial_no, int(last["create_time"]), int(last["create_time"]),
                            f"溶解氧 {do_val:.2f} mg/L < 2，水生生物窒息风险")
            elif do_val < 3:
                raise_alert("R1-DO低氧预警", "黄", serial_no, int(last["create_time"]), int(last["create_time"]),
                            f"溶解氧 {do_val:.2f} mg/L < 3，接近缺氧警戒线")
        except (TypeError, ValueError):
            pass

    # R2 EC扰动（1小时变幅>10%）
    if last.get("ec") not in (None, ""):
        hour_ago_rows = [r for r in rows if int(r["create_time"]) <= int(last["create_time"]) - 3600]
        if hour_ago_rows:
            base = hour_ago_rows[-1]
            try:
                base_ec, last_ec = float(base["ec"]), float(last["ec"])
                if base_ec > 0:
                    pct = abs(last_ec - base_ec) / base_ec
                    if pct > 0.10:
                        raise_alert("R2-EC扰动", "黄", serial_no, int(base["create_time"]), int(last["create_time"]),
                                    f"电导率1小时变幅 {pct*100:.1f}%（{base_ec:.0f} → {last_ec:.0f} μS/cm），疑似污染输入或降雨径流")
            except (TypeError, ValueError):
                pass


# ---------- 采集 ----------
def main():
    if not JTR_TOKEN:
        raise SystemExit("缺少 JTR_TOKEN secret")

    latest = {"updated_at": datetime.fromtimestamp(NOW, CST).strftime("%Y-%m-%d %H:%M:%S"),
              "updated_ts": NOW, "devices": []}
    today = datetime.fromtimestamp(NOW, CST).strftime("%Y-%m-%d")
    history_file = HISTORY_DIR / f"{today}.json"
    history = load_json(history_file, [])
    existing = {(h["serial_no"], int(h["create_time"])) for h in history}
    new_count = 0

    # 1. 全部设备最新快照
    list_data = api_get("school.device.list", "&page=1&listRows=100")
    for dev in list_data.get("list", []):
        d = dev.get("data") or {}
        latest["devices"].append({
            "serialNo": dev["serialNo"], "categoryName": dev["categoryName"],
            "remarks": dev.get("remarks", ""), "status": dev["status"],
            "data": d if d else None,
        })
        if d and d.get("create_time"):
            key = (dev["serialNo"], int(d["create_time"]))
            if key not in existing:
                history.append({"serial_no": dev["serialNo"], "create_time": int(d["create_time"]),
                                "device_name": dev.get("remarks") or dev["categoryName"], "payload": d})
                existing.add(key)
                new_count += 1

    # 2. 重点设备24小时时序 + 预警
    for dev in DETAIL_DEVICES:
        try:
            detail = api_get("school.device.detail", f"&deviceId={dev['deviceId']}")
            rows = sorted(detail.get("list", []), key=lambda r: int(r["create_time"]))
            for row in rows:
                key = (dev["serialNo"], int(row["create_time"]))
                if key not in existing:
                    history.append({"serial_no": dev["serialNo"], "create_time": int(row["create_time"]),
                                    "device_name": dev["name"], "payload": row})
                    existing.add(key)
                    new_count += 1
            check_alerts(dev["serialNo"], rows)
        except Exception as e:
            print(f"[{dev['name']}] detail采集失败:", e)

    # 3. 写文件
    (DATA_DIR / "latest.json").write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
    history.sort(key=lambda h: int(h["create_time"]))
    history_file.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    (DATA_DIR / "alert_state.json").write_text(json.dumps(alert_state), encoding="utf-8")
    (DATA_DIR / "alerts.json").write_text(json.dumps(alerts_log, ensure_ascii=False), encoding="utf-8")
    print(f"新增 {new_count} 条记录 | 历史文件 {len(history)} 条 | {datetime.fromtimestamp(NOW, CST).strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
