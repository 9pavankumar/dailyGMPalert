import pandas as pd
import requests
import datetime
from io import StringIO

# Telegram credentials (✅ replace with GitHub Secrets later)
BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

today = datetime.date.today().strftime("%d-%m-%y")

def fetch_ipo_data():
    url = "https://ipowatch.in/gmp/"
    response = requests.get(url)
    response.raise_for_status()
    html = response.text

    # ✅ Fix FutureWarning by using StringIO
    tables = pd.read_html(StringIO(html))

    ipo_df = tables[0].copy()
    ipo_df.columns = ipo_df.columns.str.strip()

    # ✅ Clean up name and remove trailing "U"
    ipo_df.loc[:, "Name"] = ipo_df["Name"].str.replace(r"\s*U$", "", regex=True).str.strip()

    # Filter only upcoming IPOs
    ipo_df = ipo_df[ipo_df["Name"].str.contains("IPO", case=False, na=False)]

    # Extract required columns safely
    columns = ["Name", "GMP", "Price", "IPO Size", "Open", "Close"]
    ipo_df = ipo_df[[col for col in columns if col in ipo_df.columns]]

    return ipo_df

def format_message(ipo_df):
    message = f"📢 IPO Updates - {today}\n\n🔜 Upcoming IPOs\n"

    for i, row in ipo_df.iterrows():
        message += f"{i+1}. {row['Name']}\n"

    message += "\n📊 Details\n\n"

    for i, row in ipo_df.iterrows():
        gmp = str(row.get("GMP", "₹--")).replace("₹", "₹").strip()
        issue_size = row.get("IPO Size", "-")
        open_date = row.get("Open", "-")
        close_date = row.get("Close", "-")

        message += (
            f"🔜 {row['Name']}\n"
            f"💰 Issue Size: ₹{issue_size} Cr\n"
            f"📈 GMP: {gmp}\n"
            f"🗓 {open_date}–{close_date} | Upcoming\n\n"
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

        message = format_message(ipo_df)
        send_telegram_message(message)

    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    main()
