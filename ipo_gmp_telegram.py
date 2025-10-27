# ipo_gmp_telegram.py

import pandas as pd
from playwright.sync_api import sync_playwright
import datetime
import requests
import os

# ---------- TELEGRAM CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send_telegram_message(message):
    """Send formatted message to Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.get(url, params=params)
        print("Telegram Response:", resp.text)
    except Exception as e:
        print("❌ Telegram Send Error:", e)


# ---------- FETCH IPO DATA ----------
def fetch_ipo_data():
    url = "https://www.investorgain.com/report/live-ipo-gmp/331/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()

    tables = pd.read_html(html)
    if not tables:
        raise ValueError("No tables found on the page")

    ipo_df = tables[0]
    ipo_df.columns = [c.strip().replace("▲▼", "").strip() for c in ipo_df.columns]
    ipo_df = ipo_df.dropna(how="all")

    # Clean IPO Name (remove trailing 'U')
    ipo_df["Name"] = ipo_df["Name"].str.replace(r"\s*U$", "", regex=True).str.strip()

    # Remove SME and closed IPOs
    ipo_df = ipo_df[~ipo_df["Name"].str.contains("SME", case=False, na=False)]
    ipo_df = ipo_df[~ipo_df["Listing"].astype(str).str.contains("❌", na=False)]

    today = datetime.date.today()

    # Parse dates
    def parse_date(date_str):
        try:
            return datetime.datetime.strptime(date_str, "%d-%b").replace(year=today.year).date()
        except:
            return None

    ipo_df["Open_date_parsed"] = ipo_df["Open"].apply(parse_date)
    ipo_df["Close_date_parsed"] = ipo_df["Close"].apply(parse_date)
    ipo_df = ipo_df[ipo_df["Close_date_parsed"].notnull()]
    ipo_df = ipo_df[ipo_df["Close_date_parsed"] >= today]  # include future IPOs

    # Convert IPO Size to numeric (in Cr)
    ipo_df["IPO_Size_num"] = ipo_df["IPO Size"].apply(
        lambda x: float(str(x).replace(",", "")) if pd.notnull(x) else 0
    )
    ipo_df = ipo_df[ipo_df["IPO_Size_num"] > 400]

    # Extract GMP Value and Percentage directly from website
    def parse_gmp(gmp_str):
        gmp_val, gmp_pct = 0.0, None
        try:
            if pd.isnull(gmp_str):
                return gmp_val, gmp_pct
            text = str(gmp_str)
            if "₹" in text:
                gmp_val = float(text.split("₹")[1].split()[0])
            if "(" in text and "%" in text:
                gmp_pct = text.split("(")[1].split("%")[0].strip()
            return gmp_val, gmp_pct
        except Exception:
            return gmp_val, gmp_pct

    gmp_parsed = ipo_df["GMP"].apply(parse_gmp)
    ipo_df["GMP_val"] = gmp_parsed.apply(lambda x: x[0])
    ipo_df["GMP_pct"] = gmp_parsed.apply(lambda x: x[1])

    ipo_df = ipo_df[ipo_df["GMP_val"] > 8.5]

    # Weighted ranking: 70% IPO Size + 30% GMP
    ipo_df["IPO_Size_norm"] = ipo_df["IPO_Size_num"] / ipo_df["IPO_Size_num"].max()
    ipo_df["GMP_norm"] = ipo_df["GMP_val"] / ipo_df["GMP_val"].max()
    ipo_df["Weighted_Score"] = 0.7 * ipo_df["IPO_Size_norm"] + 0.3 * ipo_df["GMP_norm"]

    ipo_df = ipo_df.sort_values(by="Weighted_Score", ascending=False).reset_index(drop=True)
    ipo_df["Rank"] = ipo_df.index + 1

    return ipo_df


# ---------- FORMAT MESSAGE ----------
def format_message(ipo_df):
    today_str = datetime.date.today().strftime("%d-%m-%y")
    message = f"📢 IPO Updates - {today_str}\n\n"

    if ipo_df.empty:
        message += "No IPOs available today.\n"
        return message

    message += "🔜 Upcoming IPOs - Order of apply \n"
    for idx, row in ipo_df.iterrows():
        message += f"{row['Rank']}. {row['Name']} (Opens: {row['Open']}, Closes: {row['Close']})\n"

    message += "\n📊 Details\n\n"
    for idx, row in ipo_df.iterrows():
        gmp_info = f"₹{row['GMP_val']:.0f}"
        if pd.notnull(row.get("GMP_pct")):
            gmp_info += f" ({row['GMP_pct']}%)"

        message += (
            f"🔜 {row['Name']}\n"
            f"💰 Issue Size: ₹{row['IPO_Size_num']} Cr\n"
            f"📈 GMP: {gmp_info} | 📊 Sub: {row['Sub']}\n"
            f"🗓 {row['Open']}–{row['Close']} | Upcoming\n\n"
        )

    return message


# ---------- MAIN ----------
if __name__ == "__main__":
    try:
        df = fetch_ipo_data()
        msg = format_message(df)
        send_telegram_message(msg)
    except Exception as e:
        send_telegram_message(f"❌ IPO Update Failed:\n{e}")
        print("Error:", e)
