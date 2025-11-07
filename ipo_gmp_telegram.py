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
    params = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.get(url, params=params)
        print("Telegram Response:", resp.text)
    except Exception as e:
        print("❌ Telegram Send Error:", e)


def safe_to_float(x):
    """Safely convert IPO size to float"""
    try:
        val = str(x).replace(",", "").replace("–", "").replace("-", "").strip()
        if val == "" or val.lower() in ["nan", "none"]:
            return 0.0
        return float(val)
    except Exception:
        return 0.0


def fetch_ipo_data():
    """Fetch and process IPO data"""
    url = "https://www.investorgain.com/report/live-ipo-gmp/331/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()

    # ✅ Parse the table using only allowed parsers
    tables = pd.read_html(html, flavor=["lxml", "html5lib"])
    if not tables:
        raise ValueError("No tables found on the page")

    ipo_df = tables[0].copy()
    ipo_df.columns = [c.strip().replace("▲▼", "").strip() for c in ipo_df.columns]
    ipo_df = ipo_df.dropna(how="all")

    # ✅ Clean Name
    ipo_df["Name"] = ipo_df["Name"].astype(str).str.replace(r"\s*U$", "", regex=True).str.strip()

    # ✅ Remove SME and closed IPOs
    ipo_df = ipo_df[~ipo_df["Name"].str.contains("SME", case=False, na=False)]
    ipo_df = ipo_df[~ipo_df["Listing"].astype(str).str.contains("❌", na=False)]

    today = datetime.date.today()

    # ✅ Parse open and close dates
    def parse_date(date_str):
        try:
            return datetime.datetime.strptime(date_str, "%d-%b").replace(year=today.year).date()
        except Exception:
            return None

    ipo_df["Open_date_parsed"] = ipo_df["Open"].apply(parse_date)
    ipo_df["Close_date_parsed"] = ipo_df["Close"].apply(parse_date)
    ipo_df = ipo_df[ipo_df["Close_date_parsed"].notnull()]

    # ✅ Convert IPO Size safely
    ipo_df["IPO_Size_num"] = ipo_df["IPO Size"].apply(safe_to_float)

    # ✅ Parse GMP values
    def parse_gmp(gmp_str):
        gmp_val, gmp_pct = 0.0, None
        try:
            if pd.isnull(gmp_str):
                return gmp_val, gmp_pct
            text = str(gmp_str)
            if "₹" in text:
                val = text.split("₹")[1].split()[0]
                val = val.replace("-", "").replace("–", "").strip()
                if val.replace(".", "", 1).isdigit():
                    gmp_val = float(val)
            if "(" in text and "%" in text:
                gmp_pct = text.split("(")[1].split("%")[0].strip()
            return gmp_val, gmp_pct
        except Exception:
            return gmp_val, gmp_pct

    gmp_parsed = ipo_df["GMP"].apply(parse_gmp)
    ipo_df["GMP_val"] = gmp_parsed.apply(lambda x: x[0])
    ipo_df["GMP_pct"] = gmp_parsed.apply(lambda x: x[1])

    # ✅ Apply minimum filters for all IPOs
    ipo_df = ipo_df[(ipo_df["IPO_Size_num"] > 400) & (ipo_df["GMP_val"] > 9.5)].copy()

    # ✅ Weighted rank (applied before splitting)
    ipo_df["IPO_Size_norm"] = ipo_df["IPO_Size_num"] / ipo_df["IPO_Size_num"].max()
    ipo_df["GMP_norm"] = ipo_df["GMP_val"] / ipo_df["GMP_val"].max()
    ipo_df["Weighted_Score"] = 0.7 * ipo_df["IPO_Size_norm"] + 0.3 * ipo_df["GMP_norm"]

    # ✅ Split IPOs into current & upcoming based on date
    current_df = ipo_df[
        (ipo_df["Open_date_parsed"] <= today) & (ipo_df["Close_date_parsed"] >= today)
    ].copy()
    upcoming_df = ipo_df[ipo_df["Open_date_parsed"] > today].copy()

    # ✅ Sort and rank both groups
    current_df = current_df.sort_values(by="Weighted_Score", ascending=False).reset_index(drop=True)
    current_df["Rank"] = current_df.index + 1

    upcoming_df = upcoming_df.sort_values(by="Weighted_Score", ascending=False).reset_index(drop=True)
    upcoming_df["Rank"] = upcoming_df.index + 1

    return current_df, upcoming_df


def format_message(current_df, upcoming_df):
    """Format the Telegram message"""
    today_str = datetime.datetime.now().strftime("%d-%m-%Y")
    message = f"📢 IPO Updates - {today_str}\n\n"

    # ✅ Current IPOs
    message += "✅ <b>IPOs to Apply Now</b>\n\n"
    if current_df.empty:
        message += "No IPOs available to apply now.\n\n"
    else:
        for _, row in current_df.iterrows():
            message += f"{row['Rank']}. {row['Name']} (Closes: {row['Close']})\n"

    # ✅ Upcoming IPOs
    message += "\n🚀 <b>Upcoming IPOs</b>\n\n"
    if upcoming_df.empty:
        message += "No upcoming IPOs found.\n"
    else:
        for _, row in upcoming_df.iterrows():
            message += f"{row['Rank']}. {row['Name']} (Opens: {row['Open']})\n"

    # ✅ Details for Current IPOs
    message += "\n📊 <b>Current IPOs Details</b>\n\n"
    for _, row in current_df.iterrows():
        gmp_info = f"₹{row['GMP_val']:.0f}"
        if pd.notnull(row.get("GMP_pct")):
            gmp_info += f" ({row['GMP_pct']}%)"
        message += (
            f"🏦 {row['Name']}\n"
            f"💰 Issue Size: ₹{row['IPO_Size_num']} Cr\n"
            f"📈 GMP: {gmp_info}\n"
            f"📊 Sub: {row['Sub']}\n"
            f"🗓 {row['Open']}–{row['Close']}\n\n"
        )

    # ✅ Details for Upcoming IPOs
    message += "\n📊 <b>Upcoming IPOs Details</b>\n\n"
    for _, row in upcoming_df.iterrows():
        gmp_info = f"₹{row['GMP_val']:.0f}"
        if pd.notnull(row.get("GMP_pct")):
            gmp_info += f" ({row['GMP_pct']}%)"
        message += (
            f"🚀 {row['Name']}\n"
            f"💰 Issue Size: ₹{row['IPO_Size_num']} Cr\n"
            f"📈 GMP: {gmp_info}\n"
            f"📊 Sub: {row['Sub']}\n"
            f"🗓 {row['Open']}–{row['Close']}\n\n"
        )

    return message


# ---------- MAIN ----------
if __name__ == "__main__":
    try:
        current_df, upcoming_df = fetch_ipo_data()
        msg = format_message(current_df, upcoming_df)
        send_telegram_message(msg)
    except Exception as e:
        send_telegram_message(f"❌ IPO Update Failed:\n{e}")
        print("Error:", e)
