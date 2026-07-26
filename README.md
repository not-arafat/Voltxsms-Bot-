# 🤖 VoltX Virtual Number & OTP Telegram Bot

A high-performance, asynchronous Telegram bot built with Python (`python-telegram-bot` v20+ & `aiohttp`) for allocating virtual phone numbers and automatically receiving/forwarding OTPs in real-time.

---

## ✨ Features

* **⚡ Real-time OTP Polling:** Background task polls API every 3 seconds to catch and deliver incoming OTPs instantly.
* **📱 One-Click Copy Buttons:** Utilizes Telegram's native `copy_text` inline buttons so users can copy phone numbers and OTP codes with a single click.
* **🔒 Privacy Protection (Number Masking):** Automatically masks phone numbers (e.g., `224***5937`) when forwarding OTP alerts to public groups.
* **🧹 Smart OTP Extraction:** Extracts clean 4–8 digit OTP codes using regex, stripping away dashes (`-`) and spaces (e.g., `12-34` → `1234`).
* **👥 Admin Panel Support:** Exclusive admin options for user `7503077434` (Manage Services, Broadcast, Withdraw, User Count).
* **🔄 Interactive Control:** Allows users to easily request new numbers in the same range using the "Change Number" callback button.

---

## 🛠️ Prerequisites & Installation

### 1. Requirements
* Python 3.8 or higher
* Valid Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
* Active API Access Key (`mauthapi`)

### 2. Install Dependencies
Run the following command in your terminal:

```bash
python -m pip install python-telegram-bot aiohttp