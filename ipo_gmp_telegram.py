#!/usr/bin/env python3
"""
ipo_gmp_telegram.py

Fetches IPO GMP data from investorgain, parses the table(s), filters upcoming/current IPOs,
formats a summary and posts it to a Telegram chat via BOT_TOKEN and CHAT_ID environment vars.
"""

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit(
        "Missing dependency 'beautifulsoup4'. Install with:\n"
        "  pip install beautifulsoup4\n"
        "or add 'beautifulsoup4' to requirements.txt and re-run the workflow."
    )

import os
import sys
import re
import requests
import pandas as pd
import datetime
from io import StringIO
import html

# CONFIG: update if the source URL changes
TARGET_URL = "https://www.investorgain.com/report/live-ipo-gmp/331/"

# Environment secrets expected in GH Actions
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

USER_AGENT = "dailyGMPalert-bot/1.0 (+https://github.com/9pavankumar/dailyGMPalert)"

def fetch_html(url, timeout=15):
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text

def save_debug_html(html_text, path="./ipo_page_debug.html"):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_text)
        print(f"Saved fetched HTML to: {path}")
    except Exception as e:
        print(f"Failed to save debug HTML: {e}")

def find_and_parse_tables(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    dfs = []
    for t in tables:
        table_html = str(t)
        try:
            parsed = pd.read_html(StringIO(table_html))
            if parsed:
                dfs.extend(parsed)
        except ValueError:
            # fallback: manual parse
            rows = []
            for tr in t.find_all("tr"):
                cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cols:
                    rows.append(cols)
            if rows:
                maxcols = max(len(r) for r in rows)
                norm = [r + [""] * (maxcols - len(r)) for r in rows]
                if len(norm) >= 2:
                    df = pd.DataFrame(norm[1:], columns=norm[0])
                else:
                    df = pd.DataFrame(norm)
                dfs.append(df)
    return dfs

def ci_col(df, candidates):
    """
    Case-insensitive column find that tolerates non-string column labels.
    Returns the original column label (not its string).
    """
    # Map lowercase string version -> original label
    cols_lower = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        key = str(cand).lower()
        if key in cols_lower:
            return cols_lower[key]
    # fallback: substring match against stringified column names
    for col in df.columns:
        col_s = str(col).lower()
        for cand in candidates:
            if str(cand).lower() in col_s:
                return col
    return None

def parse_date_flexible(s, today=None):
    if pd.isnull(s):
        return None
    s = str(s).strip()
    if not s:
        return None
    if today is None:
        today = datetime.date.today()

    patterns = ["%d-%b-%Y", "%d-%b-%y", "%d-%b", "%d %b %Y", "%d %b %y", "%d %b"]
    for p in patterns:
        try:
            dt = datetime.datetime.strptime(s, p)
            if "%Y" not in p and "%y" not in p:
                dt = dt.replace(year=today.year)
            return dt.date()
        except Exception:
            continue

    try:
        parts = s.replace(",", " ").strip()
        dt = datetime.datetime.strptime(parts, "%d %B")
        return dt.replace(year=today.year).date()
    except Exception:
        pass

    m = re.search(r"(\d{1,2})\s*[-/ ]\s*([A-Za-z]{3,})", s)
    if m:
        day = int(m.group(1))
        mon = m.group(2)
        try:
            dt = datetime.datetime.strptime(f"{day} {mon} {today.year}", "%d %b %Y")
            return dt.date()
        except Exception:
            pass

    return None

def parse_size_to_cr(s):
    if pd.isnull(s):
        return None
    s = str(s).strip()
    if not s or s in ["-", "—", "–"]:
        return None
    s = s.replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    s = s.replace(",", "").replace(" ", "")
    m_cr = re.match(r"^([0-9.]+)(?:cr|Cr|CR)$", s)
    if m_cr:
        return float(m_cr.group(1))
    m_l = re.match(r"^([0-9.]+)(?:l|L|lakh|Lakh)$", s)
    if m_l:
        return float(m_l.group(1)) / 100.0
    try:
        val = float(s)
        if val > 1000:
            return val / 1e7
        return val
    except Exception:
        return None

def parse_gmp_field(s):
    if pd.isnull(s):
        return "₹-- (0.00%)"
    raw = str(s).strip()
    if not raw or raw in ["-", "—", "–"]:
        return "₹-- (0.00%)"
    rupee = None
    percent = None
    m = re.search(r"₹\s*([0-9.,]+)", raw)
    if m:
        rupee = m.group(1).replace(",", "")
    # look for percent in several ways
    m_pct = re.search(r"([+-]?[0-9.,]+)\s*%|\(([+-]?[0-9.,]+)%\)", raw)
    if m_pct:
        for g in m_pct.groups():
            if g:
                percent = g.replace(",", "")
                break
    rupee_disp = f"₹{rupee}" if rupee else "₹--"
    percent_disp = f"({percent}%)" if percent else "(0.00%)"
    return f"{rupee_disp} {percent_disp}"

def normalize_df(df):
    df = df.copy()
    col_name = ci_col(df, ["Name", "Company", "Issue Name"])
    col_open = ci_col(df, ["Open", "Opening", "Open Date"])
    col_close = ci_col(df, ["Close", "Closing", "Close Date"])
    col_size = ci_col(df, ["IPO Size", "Issue Size", "Size"])
    col_gmp = ci_col(df, ["GMP", "Grey Market Premium", "GM P"])
    col_sub = ci_col(df, ["Sub", "Subscription", "Subs"])
    col_listing = ci_col(df, ["Listing", "List", "Status"])

    def safe_col(df_local, name):
        if name is not None and name in df_local.columns:
            return df_local[name]
        return pd.Series([""] * len(df_local))

    df_std = pd.DataFrame()
    df_std["Name"] = safe_col(df, col_name)
    df_std["Open"] = safe_col(df, col_open)
    df_std["Close"] = safe_col(df, col_close)
    df_std["IPO Size"] = safe_col(df, col_size)
    df_std["GMP"] = safe_col(df, col_gmp)
    df_std["Sub"] = safe_col(df, col_sub)
    df_std["Listing"] = safe_col(df, col_listing)

    df_std["Name"] = df_std["Name"].astype(str).str.replace(r"\s*U$", "", regex=True).str.strip()

    today = datetime.date.today()
    df_std["Close_date_parsed"] = df_std["Close"].apply(lambda x: parse_date_flexible(x, today=today))
    df_std["Open_date_parsed"] = df_std["Open"].apply(lambda x: parse_date_flexible(x, today=today))

    df_std = df_std[~df_std["Name"].astype(str).str.contains("SME", case=False, na=False)]
    df_std = df_std[~df_std["Listing"].astype(str).str.contains("❌", na=False)]

    df_std["IPO_Size_num"] = df_std["IPO Size"].apply(parse_size_to_cr)
    df_std["GMP_display"] = df_std["GMP"].apply(parse_gmp_field)

    df_std = df_std.fillna("")

    try:
        df_std["IPO_Size_sort"] = df_std["IPO_Size_num"].apply(lambda x: float(x) if pd.notnull(x) and x != "" else 0.0)
    except Exception:
        df_std["IPO_Size_sort"] = 0.0
    df_std = df_std.sort_values(by="IPO_Size_sort", ascending=False).reset_index(drop=True)
    df_std["Rank"] = df_std.index + 1

    return df_std

def filter_relevant(df):
    today = datetime.date.today()
    future_limit = today + datetime.timedelta(days=5)
    cond_close = df["Close_date_parsed"].apply(lambda d: isinstance(d, datetime.date) and (today - datetime.timedelta(days=1) <= d <= future_limit))
    cond_open_future = df["Open_date_parsed"].apply(lambda d: isinstance(d, datetime.date) and (d > today))
    filtered = df[cond_close | cond_open_future].reset_index(drop=True)
    return filtered

def format_message_html(df):
    today_str = datetime.date.today().strftime("%d-%b-%Y")
    if df.empty:
        return f"<b>📢 IPO Updates - {html.escape(today_str)}</b>\n\nNo upcoming/current IPOs found."

    lines = []
    lines.append(f"<b>📢 IPO Updates - {html.escape(today_str)}</b>")
    lines.append("")
    lines.append("🔜 <b>Upcoming / Current IPOs</b>")
    for _, row in df.iterrows():
        name = html.escape(str(row["Name"]))
        opend = html.escape(str(row["Open"]))
        closed = html.escape(str(row["Close"]))
        lines.append(f"{row['Rank']}. <b>{name}</b> (Opens: {opend}, Closes: {closed})")

    lines.append("")
    lines.append("📊 <b>Details</b>")
    for _, row in df.iterrows():
        name = html.escape(str(row["Name"]))
        size = row.get("IPO_Size_num")
        size_disp = f"{size:,.2f} Cr" if (size not in [None, ""] and pd.notnull(size)) else str(row.get("IPO Size", "") or "--")
        gmp = html.escape(str(row.get("GMP_display", "₹-- (0.00%)")))
        sub = html.escape(str(row.get("Sub", "")))
        opend = html.escape(str(row.get("Open", "")))
        closed = html.escape(str(row.get("Close", "")))
        listing = html.escape(str(row.get("Listing", "")))
        lines.append(f"🔸 <b>{name}</b>")
        lines.append(f"• 💰 Issue Size: {size_disp}")
        lines.append(f"• 📈 GMP: {gmp} | 📊 Sub: {sub or '--'}")
        lines.append(f"• 🗓 {opend} – {closed} | Listing: {listing or '--'}")
        lines.append("")

    return "\n".join(lines).strip()

def send_telegram_message(message, parse_mode="HTML"):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN and/or CHAT_ID environment variables not set.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": parse_mode, "disable_web_page_preview": True}
    resp = requests.post(url, json=payload, timeout=15)
    try:
        resp.raise_for_status()
        print("✅ Telegram message sent successfully.")
        return True
    except Exception:
        print(f"❌ Telegram Error: {resp.status_code} {resp.text}")
        return False

def main():
    try:
        html_text = fetch_html(TARGET_URL)
    except Exception as e:
        print(f"❌ Failed to fetch {TARGET_URL}: {e}")
        sys.exit(1)

    dfs = find_and_parse_tables(html_text)
    if not dfs:
        print("No tables found in the fetched page.")
        save_debug_html(html_text, path="./ipo_page_debug.html")
        sys.exit(0)

    dfs_clean = [d.dropna(how="all", axis=1).dropna(how="all", axis=0) for d in dfs if not d.dropna(how="all", axis=1).empty]
    if not dfs_clean:
        print("Parsed tables exist but all are empty after cleanup.")
        save_debug_html(html_text, path="./ipo_page_debug.html")
        sys.exit(0)

    best = max(dfs_clean, key=lambda x: (x.shape[0] * x.shape[1]))
    print(f"Using table with shape: {best.shape}")

    # If the best table is just a single cell, treat as no useful data and save debug HTML
    if best.shape == (1, 1):
        single_val = best.iat[0, 0]
        print(f"Best table is a single cell: {single_val!r} — treating as no useful data.")
        save_debug_html(html_text, path="./ipo_page_debug.html")
        sys.exit(0)

    try:
        df_std = normalize_df(best)
    except Exception as e:
        print(f"❌ Failed to normalize parsed table: {e}")
        save_debug_html(html_text, path="./ipo_page_debug.html")
        sys.exit(1)

    df_filtered = filter_relevant(df_std)
    if df_filtered.empty:
        print("No relevant IPOs after filtering (close/open dates).")
        msg = format_message_html(df_filtered)
        send_telegram_message(msg)
        sys.exit(0)

    msg = format_message_html(df_filtered)
    ok = send_telegram_message(msg)
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
