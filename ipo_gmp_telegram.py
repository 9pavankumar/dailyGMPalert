#!/usr/bin/env python3
"""
ipo_gmp_telegram.py

Fetches IPO GMP data from investorgain, parses the table(s), filters upcoming/current IPOs,
formats a summary and posts it to a Telegram chat via BOT_TOKEN and CHAT_ID environment vars.

Requirements:
- requests
- pandas
- beautifulsoup4
- lxml (recommended)
- html5lib (fallback)
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
    Case-insensitive column find. Returns first matching column name in df for candidates list.
    """
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    # fallback: try to match by substring
    for col in df.columns:
        for cand in candidates:
            if cand.lower() in str(col).lower():
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

    # Try multiple formats. If no year, assume current year.
    patterns = ["%d-%b-%Y", "%d-%b-%y", "%d-%b", "%d %b %Y", "%d %b %y", "%d %b"]
    for p in patterns:
        try:
            dt = datetime.datetime.strptime(s, p)
            if "%Y" not in p and "%y" not in p:
                dt = dt.replace(year=today.year)
            return dt.date()
        except Exception:
            continue

    # Try parsing with month names spelled out
    try:
        # e.g., "31 Oct"
        parts = s.replace(",", " ").strip()
        dt = datetime.datetime.strptime(parts, "%d %B")
        return dt.replace(year=today.year).date()
    except Exception:
        pass

    # Last-resort: extract numbers for day and month by name
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
    """
    Convert various IPO Size strings to numeric value in Crore (Cr).
    Examples:
      "250 Cr" -> 250.0
      "6,50,00,000" -> try to parse but fallback to NaN
      "650 Lakh" -> 6.5 (since 100 Lakh = 1 Cr)
    """
    if pd.isnull(s):
        return None
    s = str(s).strip()
    if not s or s in ["-", "—", "–"]:
        return None
    s = s.replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    # normalize commas and weird separators
    s = s.replace(",", "").replace(" ", "")
    # common patterns: end with Cr, L, Lakh, lakh
    m_cr = re.match(r"^([0-9.]+)(?:cr|Cr|CR)$", s)
    if m_cr:
        return float(m_cr.group(1))
    m_l = re.match(r"^([0-9.]+)(?:l|L|lakh|Lakh)$", s)
    if m_l:
        # convert Lakh to Cr (1 Cr = 100 Lakh)
        return float(m_l.group(1)) / 100.0
    # if plain number, assume it's already in Crore if value small; otherwise try heuristic:
    try:
        val = float(s)
        # If value seems huge (e.g., >10000) maybe it's in rupees, convert to crores:
        if val > 1000:  # heuristic threshold
            # value might be in rupees; convert rupees to crores: /1e7
            return val / 1e7
        return val
    except Exception:
        return None

def parse_gmp_field(s):
    """
    Return formatted display and try to extract numeric rupee + percent if needed.
    """
    if pd.isnull(s):
        return "₹-- (0.00%)"
    raw = str(s).strip()
    if not raw or raw in ["-", "—", "–"]:
        return "₹-- (0.00%)"
    # Try to extract rupee and percent: e.g. "₹ 50 (2.00%)" or "₹50 / 2%"
    rupee = None
    percent = None
    m = re.search(r"₹\s*([0-9.,]+)", raw)
    if m:
        rupee = m.group(1).replace(",", "")
    m2 = re.search(r"([0-9.,]+)\s*%|\(([-+]?[0-9.,]+)%\)", raw)
    if m2:
        # pick first non-empty group
        for g in m2.groups():
            if g:
                percent = g.replace(",", "")
                break
    # Build display
    rupee_disp = f"₹{rupee}" if rupee else "₹--"
    percent_disp = f"({percent}%)" if percent else "(0.00%)"
    return f"{rupee_disp} {percent_disp}"

def normalize_df(df):
    # Lower/strip columns for easier matching; but keep originals
    df = df.copy()
    # find columns with various names
    col_name = ci_col(df, ["Name", "Company", "Issue Name"])
    col_open = ci_col(df, ["Open", "Opening", "Open Date"])
    col_close = ci_col(df, ["Close", "Closing", "Close Date"])
    col_size = ci_col(df, ["IPO Size", "Issue Size", "Size"])
    col_gmp = ci_col(df, ["GMP", "Grey Market Premium", "GM P"])
    col_sub = ci_col(df, ["Sub", "Subscription", "Subs"])
    col_listing = ci_col(df, ["Listing", "List", "Status"])

    # Create standardized columns
    def safe_col(df, name):
        return df[name] if name and name in df.columns else pd.Series([""] * len(df))

    df_std = pd.DataFrame()
    df_std["Name"] = safe_col(df, col_name)
    df_std["Open"] = safe_col(df, col_open)
    df_std["Close"] = safe_col(df, col_close)
    df_std["IPO Size"] = safe_col(df, col_size)
    df_std["GMP"] = safe_col(df, col_gmp)
    df_std["Sub"] = safe_col(df, col_sub)
    df_std["Listing"] = safe_col(df, col_listing)

    # Clean Name: remove trailing 'U' or other markers
    df_std["Name"] = df_std["Name"].astype(str).str.replace(r"\s*U$", "", regex=True).str.strip()

    # parse dates
    today = datetime.date.today()
    df_std["Close_date_parsed"] = df_std["Close"].apply(lambda x: parse_date_flexible(x, today=today))
    df_std["Open_date_parsed"] = df_std["Open"].apply(lambda x: parse_date_flexible(x, today=today))

    # Filter SME and closed ones
    df_std = df_std[~df_std["Name"].astype(str).str.contains("SME", case=False, na=False)]
    df_std = df_std[~df_std["Listing"].astype(str).str.contains("❌", na=False)]

    # Parse IPO Size numeric in Crores
    df_std["IPO_Size_num"] = df_std["IPO Size"].apply(parse_size_to_cr)

    # Parse GMP display
    df_std["GMP_display"] = df_std["GMP"].apply(parse_gmp_field)

    # Replace NaNs and fill blanks
    df_std = df_std.fillna("")

    # Sort by IPO size desc, NaN -> 0
    try:
        df_std["IPO_Size_sort"] = df_std["IPO_Size_num"].apply(lambda x: float(x) if pd.notnull(x) else 0.0)
    except Exception:
        df_std["IPO_Size_sort"] = 0.0
    df_std = df_std.sort_values(by="IPO_Size_sort", ascending=False).reset_index(drop=True)
    df_std["Rank"] = df_std.index + 1

    return df_std

def filter_relevant(df):
    """
    Keep IPOs that are:
      - Close date between yesterday and next 5 days
      - OR Open date in future (upcoming)
    """
    today = datetime.date.today()
    future_limit = today + datetime.timedelta(days=5)
    cond_close = df["Close_date_parsed"].apply(lambda d: isinstance(d, datetime.date) and (today - datetime.timedelta(days=1) <= d <= future_limit))
    cond_open_future = df["Open_date_parsed"].apply(lambda d: isinstance(d, datetime.date) and (d > today))
    filtered = df[cond_close | cond_open_future].reset_index(drop=True)
    return filtered

def format_message_html(df):
    # HTML formatted message
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
        lines.append("")  # blank line between entries

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
        # Exit 0 to avoid failing scheduled workflow when there's simply no data.
        sys.exit(0)

    # Choose the first reasonable dataframe (prefer wider one)
    # Remove completely empty dfs
    dfs_clean = [d.dropna(how="all", axis=1).dropna(how="all", axis=0) for d in dfs if not d.dropna(how="all", axis=1).empty]
    if not dfs_clean:
        print("Parsed tables exist but all are empty after cleanup.")
        save_debug_html(html_text, path="./ipo_page_debug.html")
        sys.exit(0)

    # pick largest table by columns*rows
    best = max(dfs_clean, key=lambda x: (x.shape[0] * x.shape[1]))
    print(f"Using table with shape: {best.shape}")

    try:
        df_std = normalize_df(best)
    except Exception as e:
        print(f"❌ Failed to normalize parsed table: {e}")
        save_debug_html(html_text, path="./ipo_page_debug.html")
        sys.exit(1)

    df_filtered = filter_relevant(df_std)
    if df_filtered.empty:
        print("No relevant IPOs after filtering (close/open dates).")
        # Still send summary or just exit: here we send a small summary
        msg = format_message_html(df_filtered)
        send_telegram_message(msg)
        sys.exit(0)

    msg = format_message_html(df_filtered)
    ok = send_telegram_message(msg)
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
