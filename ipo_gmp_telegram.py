import pandas as pd
import requests
import datetime
from io import StringIO
import os

# ✅ Get secrets from GitHub Actions
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

today = datetime.date.today().strftime("%d-%m-%y")


def fetch_ipo_data():
    url = "https://www.investorgain.com/report/live-ipo-gmp/331/"
    response = requests.get(url)
    response.raise_for_status()
    html = response.text

    # ✅ Avoid FutureWarning
    tables = pd.read_html(StringIO(html))
    if not tables:
        raise ValueError("No tables found on the page")

    ipo_df = tables[0].copy()
    ipo_df.columns = [c.strip().replace("▲▼", "").strip() for c in ipo_df.columns]
    ipo_df = ipo_df.dropna(how="all")

    # ✅ Remove SME and closed IPOs
    ipo_df = ipo_df[~ipo_df["Name"].astype(str).str.contains("SME", case=False, na=False)]

    # ✅ Remove ❌ listings (already listed or closed)
    ipo_df = ipo_df[~ipo_df["Listing"].astype(str).str.contains("❌", na=False)]

    # ✅ Clean trailing “U” from Name (Upcoming flag)
    ipo_df.loc[:, "Name"] = ipo_df["Name"].str.replace(r"\s*U$", "", regex=True).str.strip()

    # ✅ Add parsed close date
    today_date = datetime.date.today()
    def parse_date(date_str):
        try:
            return datetime.datetime.strptime(date_str, "%d-%b").replace(year=today_date.year).date()
        except:
            return None

    ipo_df["Close_date_parsed"] = ipo_df["Close"].apply(parse_date)
    ipo_df["Open_date_parsed"] = ipo_df["Open"].apply(parse_date)

    # ✅ Keep IPOs that are:
    #   1️⃣ Open or closing today/yesterday
    #   2️⃣ Upcoming in next 5 days
    future_limit = today_date + datetime.timedelta(days=5)
    ipo_df = ipo_df[
        (ipo_df["Close_date_parsed"].between(today_date - datetime.timedelta(days=1), future_limit))
        | (ipo_df["Open_date_parsed"] > today_date)
    ]

    # ✅ Convert IPO Size to numeric
    ipo_df["IPO_Size_num"] = (
        ipo_df["IPO Size"].astype(str).str.replace(",", "").astype(float)
    )

    # ✅ Extract GMP values (₹ + %)
    def parse_gmp(gmp_str):
        if pd.isnull(gmp_str) or "₹" not in str(gmp_str):
            return "₹-- (0.00%)"
        return str(gmp_str).strip()

    ipo_df["GMP_display"] = ipo_df["GMP"].apply(parse_gmp)

    # ✅ Sort by size descending
    ipo_df = ipo_df.sort_values(by="IPO_Size_num", ascending=False).reset_index(drop=True)
    ipo_df["Rank"] = ipo_df.index + 1

    return ipo_df


def format_message(ipo_df):
    message = f"📢 IPO Updates - {today}\n\n🔜 Upcoming / Current IPOs\n"
    for i, row in ipo_df.iterrows():
        message += f"{row['Rank']}. {row['Name']} (Opens: {row['Open']}, Closes: {row['Close']})\n"

    message += "\n📊 Details\n\n"
    for _, row in ipo_df.iterrows():
        message += (
            f"🔜 {row['Name']}\n"
            f"💰 Issue Size: ₹{row['IPO_Size_num']} Cr\n"
            f"📈 GMP: {row['GMP_display']} | 📊 Sub: {row['Sub']}\n"
            f"🗓 {row['Open']}–{row['Close']} | Listing: {row['Listing']} ✅\n\n"
        )

    return message.strip()


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    response = requests.post(url, json=payload)

    if response.ok:
        print("✅ Telegram message sent successfully.")
    else:
        print(f"❌ Telegram Error: {response.text}")


def main():
    try:
        ipo_df = fetch_ipo_data()
        if ipo_df.empty:
            print("No IPO data found.")
            return

        msg = format_message(ipo_df)
        send_telegram_message(msg)
    except Exception as e:
        print(f"❌ Failed: {e}")


if __name__ == "__main__":
    main()
