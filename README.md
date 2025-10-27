# 📢 Daily IPO GMP Alert Bot

This project automatically fetches live IPO GMP (Grey Market Premium) data and sends updates to a Telegram group **twice daily** — at **9:50 AM** and **1:20 PM IST**.

## 🚀 How It Works
- Uses **Playwright** to scrape live IPO data from [InvestorGain](https://www.investorgain.com/report/live-ipo-gmp/331/)
- Processes data using **Pandas**
- Sends a formatted message to a **Telegram channel**
- Runs automatically via **GitHub Actions (Dockerized)**

## 🛠️ Setup Steps

1. **Fork or clone this repo**
2. Add repository secrets under  
   **Settings → Secrets and Variables → Actions**
   - `BOT_TOKEN` → Your Telegram bot token
   - `CHAT_ID` → Your Telegram group/chat ID
3. The workflow runs automatically every day:
   - 🕘 9:50 AM IST
   - 🕐 1:20 PM IST

To trigger manually, go to **Actions → Daily IPO Updates → Run workflow**

## 🧩 Tech Used
- Python 3.10
- Playwright (headless Chromium)
- Docker
- GitHub Actions
