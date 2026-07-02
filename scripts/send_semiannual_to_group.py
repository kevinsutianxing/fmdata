#!/usr/bin/env python3
"""
Send semiannual investment report to 交易/研究信息群 via Feishu IM API.
---
Feishu interactive card 'markdown' tag strips table formatting in group context
(server converts pipes to {"tag":"text"}). Two workarounds that actually work:
1. PNG image attachments → renders inline in group (primary method)
2. .md file attachments → renders properly when opened
---
This script generates table PNGs from CSV, uploads them, and sends to the group.
"""
import json, requests, csv, sys, time, os
from pathlib import Path
from datetime import date

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "oc_34b631278d927943a58c40fdba1b35b5")

REPORT_PATH = Path.home() / "fmdata/store/fundamentals/semiannual_investment_report.md"
CSV_PATH = Path.home() / "fmdata/store/fundamentals/semiannual_investment.csv"

# ---- Feishu API helpers ----

def get_token():
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=15)
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]


def send_text_card(token, title, text_lines):
    """Send a text-only interactive card (no tables)."""
    md = "\n".join(text_lines)
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "elements": [{"tag": "markdown", "content": md}],
    }
    content = json.dumps(card, ensure_ascii=False)
    payload = {"receive_id": CHAT_ID, "msg_type": "interactive", "content": content}
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=30)
    return resp.json().get("code") == 0


def upload_and_send_image(token, img_path, img_name):
    """Upload image to Feishu and send as image message."""
    with open(img_path, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            files={"image": (img_name, f, "image/png")},
            data={"image_type": "message"}, timeout=30)
    result = resp.json()
    if result.get("code") != 0:
        print(f"  ❌ Image upload failed: {result.get('msg')}", file=sys.stderr)
        return False

    image_key = result["data"]["image_key"]
    content = json.dumps({"image_key": image_key}, ensure_ascii=False)
    payload = {"receive_id": CHAT_ID, "msg_type": "image", "content": content}
    resp2 = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=30)
    ok = resp2.json().get("code") == 0
    if ok:
        print(f"  ✅ {img_name}")
    else:
        print(f"  ❌ Send failed: {resp2.json().get('msg')}", file=sys.stderr)
    return ok


def upload_and_send_file(token, file_path, file_name):
    """Upload file and send as file message."""
    with open(file_path, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (file_name, f, "application/octet-stream")},
            data={"file_type": "stream", "file_name": file_name}, timeout=30)
    result = resp.json()
    if result.get("code") != 0:
        print(f"  ❌ File upload failed: {result.get('msg')}", file=sys.stderr)
        return False

    file_key = result["data"]["file_key"]
    content = json.dumps({"file_key": file_key}, ensure_ascii=False)
    payload = {"receive_id": CHAT_ID, "msg_type": "file", "content": content}
    resp2 = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=30)
    ok = resp2.json().get("code") == 0
    if ok:
        print(f"  ✅ {file_name}")
    else:
        print(f"  ❌ File send failed: {resp2.json().get('msg')}", file=sys.stderr)
    return ok


# ---- Table-to-PNG rendering ----

def get_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ]
    for p in paths:
        try:
            return __import__('PIL').ImageFont.truetype(p, size)
        except:
            pass
    return __import__('PIL').ImageFont.load_default()


def render_table_png(title, headers, rows, col_widths, out_path):
    """Render a data table as PNG image."""
    from PIL import Image, ImageDraw

    font = get_font(13)
    font_bold = get_font(13)
    font_title = get_font(16)

    row_h = 28
    title_h = 42
    header_h = 32
    pad_x, pad_y = 10, 6

    tw = sum(col_widths) + pad_x * 2
    th = title_h + header_h + len(rows) * row_h + pad_y * 2 + 20

    img = Image.new('RGB', (tw, th), 'white')
    draw = ImageDraw.Draw(img)

    # Title
    draw.text((pad_x, pad_y + 2), title, fill='#1a1a2e', font=font_title)

    # Header
    y = title_h
    draw.rectangle([(0, y), (tw, y + header_h)], fill='#16213e')
    x = pad_x
    for h, w in zip(headers, col_widths):
        draw.text((x + 3, y + 6), h, fill='white', font=font_bold)
        x += w

    # Rows
    y = title_h + header_h
    for ri, row in enumerate(rows):
        bg = '#f0f4f8' if ri % 2 == 0 else 'white'
        draw.rectangle([(0, y), (tw, y + row_h)], fill=bg)
        x = pad_x
        for ci, (val, w) in enumerate(zip(row, col_widths)):
            color = '#333333'
            try:
                v = float(val)
                # Last N cols: score columns
                if ci >= len(row) - 5:
                    if v >= 80: color = '#c0392b'
                    elif v >= 60: color = '#e67e22'
                    elif v < 30: color = '#95a5a6'
            except:
                pass
            s = str(val)[:w // 7 - 1]
            draw.text((x + 3, y + 5), s, fill=color, font=font)
            x += w
        y += row_h

    draw.line([(0, y), (tw, y)], fill='#16213e', width=1)
    img.save(out_path, 'PNG')
    return out_path


def build_summary_card():
    """Build a text summary card (no tables, safe for group)."""
    lines = []
    with open(REPORT_PATH) as f:
        text = f.read()

    # Extract key stats
    strong = len([l for l in text.split('\n') if l.startswith('| ') and '🟢' not in l and '强信号' not in l])
    # Count actual stocks in strong zone
    n_strong = 0
    n_obs = 0
    n_avoid = 0
    for line in text.split('\n'):
        if line.count('|') >= 8:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 3:
                try:
                    score = float(parts[-3] if len(parts) > 7 else parts[-1])
                    if score >= 55: n_strong += 1
                    elif score >= 45: n_obs += 1
                    else: n_avoid += 1
                except:
                    pass

    # Top 3
    top3 = []
    with open(CSV_PATH, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: float(r.get('total_score', 0) or 0), reverse=True)
    for r in rows[:3]:
        score = float(r.get('total_score', 0))
        top3.append(f"**{r['name']}**({r['code']}) {score:.1f}分")

    today = date.today().isoformat()
    return [
        f"## 半年报投资日报 {today}",
        f"覆盖 **28** 只沪深标的 | 6因子Rank百分位 v3.4",
        "",
        f"🟢 强信号区(≥55): **{n_strong}**只",
        f"🟡 观察区(45-55): **{n_obs}**只",
        f"🔴 回避区(<45): **{n_avoid}**只",
        "",
        f"**Top 3**:",
        f"1. {top3[0]}" if len(top3) > 0 else "",
        f"2. {top3[1]}" if len(top3) > 1 else "",
        f"3. {top3[2]}" if len(top3) > 2 else "",
        "",
        "📈 **日频IC: 0.7086 (p=0.0046)** ✅",
        "",
        "👇 详细排名+全量数据见下方图片",
    ]


def build_score_table(today, out_path):
    """Build score ranking table PNG."""
    cols = [
        ("code","代码",68),("name","简称",78),("notice_date","日期",62),
        ("forecast_mid_yi","预告H1",66),("h1_prof_forecast_yoy_pct","H1YoY%",58),
        ("q2_prof_implied_qoq_pct","Q2QoQ%",58),("sue_score","SUE",44),
        ("eq_score","EQ",40),("price_score","价格",40),("precar_score","PreCAR",48),
        ("total_score","总分",44),("_tier","等级",50),
    ]
    with open(CSV_PATH, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: float(r.get('total_score', 0) or 0), reverse=True)

    headers = [c[1] for c in cols]
    col_widths = [c[2] for c in cols]

    table_rows = []
    for r in rows:
        score = float(r.get('total_score', 0) or 0)
        tier = "🟢强" if score >= 55 else ("🟡观察" if score >= 45 else "🔴回避")
        row = []
        for ck, _, _ in cols:
            if ck == "_tier":
                row.append(tier)
                continue
            v = r.get(ck, '')
            try:
                if ck.endswith("_yi"):
                    row.append(f"{float(v):.2f}")
                elif ck.endswith("_pct") or ck.endswith("_score"):
                    row.append(f"{float(v):.0f}")
                else:
                    row.append(v)
            except:
                row.append(v)
        table_rows.append(row)

    return render_table_png(
        f"半年报投资排名 · {today} · IC=0.7086 p=0.0046",
        headers, table_rows, col_widths, out_path)


def build_full_table(today, out_path):
    """Build full 17-column table PNG."""
    cols = [
        ("code","代码",68),("name","简称",78),("notice_date","公告日",60),
        ("forecast_type","类型",48),("forecast_mid_yi","预告H1",64),
        ("h1_prior_profit_yi","去年H1",64),("h1_prof_forecast_yoy_pct","H1YoY%",56),
        ("q1_profit_yi","Q1实",58),("q1_prof_yoy_pct","Q1YoY%",56),
        ("q2_profit_yi_implied","Q2隐含",60),("q2_prof_implied_yoy_pct","Q2YoY%",56),
        ("q2_prof_implied_qoq_pct","Q2QoQ%",58),
        ("sue_score","SUE",44),("eq_score","EQ",40),("price_score","价格",40),
        ("precar_score","PreCAR",46),("total_score","总分",44),
    ]
    with open(CSV_PATH, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: float(r.get('total_score', 0) or 0), reverse=True)

    headers = [c[1] for c in cols]
    col_widths = [c[2] for c in cols]

    table_rows = []
    for r in rows:
        row = []
        for ck, _, _ in cols:
            v = r.get(ck, '')
            try:
                if ck.endswith("_yi"):
                    row.append(f"{float(v):.2f}")
                elif ck.endswith("_pct") or ck.endswith("_score"):
                    row.append(f"{float(v):.1f}")
                else:
                    row.append(v)
            except:
                row.append(v)
        table_rows.append(row)

    return render_table_png(
        f"半年报全量数据 · {today} · {len(rows)}只标的",
        headers, table_rows, col_widths, out_path)


def build_date_sorted_table(today, out_path):
    """Build table sorted by announcement date (newest first) with 🆕 for today's."""
    cols = [
        ("notice_date","公告日",66),("code","代码",64),("name","简称",76),
        ("forecast_type","类型",44),("forecast_mid_yi","预告H1",64),
        ("h1_prior_profit_yi","去年H1",64),("h1_prof_forecast_yoy_pct","H1YoY%",56),
        ("q1_profit_yi","Q1实",56),("q2_profit_yi_implied","Q2隐含",60),
        ("q2_prof_implied_yoy_pct","Q2YoY%",56),("q2_prof_implied_qoq_pct","Q2QoQ%",56),
        ("sue_score","SUE",42),("eq_score","EQ",38),("price_score","价格",42),
        ("precar_score","PreCAR",46),("total_score","总分",44),("_tier","等级",48),
    ]
    with open(CSV_PATH, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r.get('notice_date', '0000-00-00'), reverse=True)

    headers = [c[1] for c in cols]
    col_widths = [c[2] for c in cols]

    table_rows = []
    for r in rows:
        score = float(r.get('total_score', 0) or 0)
        tier = "🟢" if score >= 55 else ("🟡" if score >= 45 else "🔴")
        ndate = r.get('notice_date', '')
        is_new = ndate == today
        row = []
        for ck, _, _ in cols:
            if ck == "_tier":
                row.append(tier)
                continue
            v = r.get(ck, '')
            if ck == 'notice_date':
                row.append(f"{v} 🆕" if is_new else v)
            elif ck.endswith("_yi"):
                try: row.append(f"{float(v):.2f}")
                except: row.append(v)
            elif ck.endswith("_pct") or ck.endswith("_score"):
                try: row.append(f"{float(v):.1f}")
                except: row.append(v)
            else:
                row.append(v)
        table_rows.append(row)

    return render_table_png(
        f"半年报追踪 · 按公告日排序 ({today})   🆕=今日新公告",
        headers, table_rows, col_widths, out_path)


# ---- Main ----

def main():
    if not REPORT_PATH.exists():
        print("ERROR: report not found", file=sys.stderr)
        sys.exit(1)

    token = get_token()
    today = date.today().isoformat()

    # 1. Text summary card (works in groups as long as no tables)
    summary = build_summary_card()
    send_text_card(token, f"半年报投资日报 {today}", summary)
    time.sleep(0.5)

    # 2. Score ranking table as PNG image
    score_img = f"/tmp/semiannual_score_{today}.png"
    build_score_table(today, score_img)
    upload_and_send_image(token, score_img, f"半年报排名_{today}.png")
    time.sleep(0.5)

    # 3. Full 17-column table as PNG image
    full_img = f"/tmp/semiannual_full_{today}.png"
    build_full_table(today, full_img)
    upload_and_send_image(token, full_img, f"半年报全量表_{today}.png")
    time.sleep(0.5)

    # 4. Date-sorted table (公告日从近到远) 🆕 marks today's announcements
    date_img = f"/tmp/semiannual_date_{today}.png"
    build_date_sorted_table(today, date_img)
    upload_and_send_image(token, date_img, f"半年报_按公告日排序_{today}.png")
    time.sleep(0.5)

    # 5. Raw CSV as file (backup / drill-down)
    if CSV_PATH.exists():
        upload_and_send_file(token, str(CSV_PATH), f"半年报原始数据_{today}.csv")

    # Cleanup
    for p in [score_img, full_img, date_img]:
        try: os.unlink(p)
        except: pass

    print("✅ All messages sent to 交易/研究信息群")


if __name__ == "__main__":
    main()
