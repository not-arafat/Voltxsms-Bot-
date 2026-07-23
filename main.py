import asyncio
import logging
import re
import os
import threading
import aiohttp
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --- FLASK SERVER SETUP (For Render & UptimeRobot) ---
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot Server is Alive and Running 24/7!", 200

def run_flask():
    # Render পরিবেশ থেকে পোর্ট নিবে, ডিফল্ট ৮০৮০
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# --- CONFIGURATION ---
BOT_TOKEN = "8345617098:AAF2vkdIWCr9qphXFQI1YEPb4fvp2XqFee4"
ADMIN_ID = 7503077434
ADMIN_USERNAME = "not_arafat"
FORWARD_GROUP_ID = -1003925783286 
OTP_GROUP_LINK = "https://t.me/mytesstgroup2"
API_KEY = "MM986GSKFJ1"
BASE_URL = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
BOT_LINK = "https://t.me/UniversaITest_Bot"
CHANNEL_LINK = "https://t.me/BackUpChannnel"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# --- DATA STORAGE ---
ALL_USERS = set()               # সকল রেজিস্টার্ড ইউজার ID
REFERRALS = {}                  # {user_id: total_referrals}
REFERRED_BY = {}                # {new_user_id: referrer_id}

USER_STATE = {}
USER_LAST_RANGE = {}            # {user_id: last_rid}
USER_ALLOCATED_NUMBERS = {}    # {user_id: {no_plus_number: country_name}}

# --- HELPER FUNCTIONS ---

def extract_clean_otp(message: str) -> str:
    if not message:
        return "N/A"
    
    match = re.search(r'\b\d{2,4}[-\s]?\d{2,4}\b', message)
    if match:
        raw_code = match.group(0)
        clean_code = re.sub(r'[-\s]', '', raw_code)
        if 4 <= len(clean_code) <= 8:
            return clean_code

    digits_only = re.findall(r'\b\d{4,8}\b', message)
    if digits_only:
        return digits_only[0]
        
    return message

def mask_number(num_str: str) -> str:
    length = len(num_str)
    if length <= 5:
        return num_str

    mid = length // 2
    masked = num_str[:mid-1] + "***" + num_str[mid+2:]
    return masked

async def api_get_number(rid: str):
    headers = {"mauthapi": API_KEY}
    url = f"{BASE_URL}/getnum"
    payload = {"rid": rid}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                return await resp.json()
        except Exception as e:
            logging.error(f"Error calling getnum: {e}")
            return None

async def api_get_otps():
    headers = {"mauthapi": API_KEY}
    url = f"{BASE_URL}/success-otp"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                return await resp.json()
        except Exception as e:
            logging.error(f"Error calling success-otp: {e}")
            return None

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("Get Number"), KeyboardButton("View Traffic")],
        [KeyboardButton("Refer"), KeyboardButton("Wallet")],
        [KeyboardButton("Support")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton("AdminPanel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- NUMBER ALLOCATION UI BUILDER ---

async def generate_and_send_number(user_id: int, rid: str, update: Update = None, query = None):
    response = await api_get_number(rid)

    if response and response.get("meta", {}).get("code") == 200:
        data = response.get("data", {})
        full_number = data.get("full_number")
        no_plus_number = data.get("no_plus_number")
        country = data.get("country", "Unknown")

        if user_id not in USER_ALLOCATED_NUMBERS:
            USER_ALLOCATED_NUMBERS[user_id] = {}
        USER_ALLOCATED_NUMBERS[user_id][no_plus_number] = country
        USER_LAST_RANGE[user_id] = rid

        msg_text = (
            f"**{country}'s Numbers Assigned:**\n"
            f"╭─────────────────╮\n"
            f"│      🌟 Waiting For OTP: \n"
            f"╰─────────────────╯"
        )

        inline_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text=f"{full_number}", copy_text={"text": full_number})],
            [
                InlineKeyboardButton(text="🔄Change Number", callback_data=f"change_num:{rid}"),
                InlineKeyboardButton(text="📩OTP Group", url=OTP_GROUP_LINK)
            ]
        ])

        if update and update.message:
            await update.message.reply_text(msg_text, reply_markup=inline_keyboard, parse_mode="Markdown")
        elif query:
            await query.message.reply_text(msg_text, reply_markup=inline_keyboard, parse_mode="Markdown")

    elif response and response.get("meta", {}).get("code") == 2946:
        err_msg = "দুঃখিত, এই রেঞ্জের নম্বর বর্তমানে স্টকে নেই (Out of Stock)।"
        if update and update.message:
            await update.message.reply_text(err_msg)
        elif query:
            await query.message.reply_text(err_msg)
    else:
        err_msg = "নম্বর বরাদ্দ করতে সমস্যা হয়েছে। সঠিক Range ব্যবহার করুন।"
        if update and update.message:
            await update.message.reply_text(err_msg)
        elif query:
            await query.message.reply_text(err_msg)

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)

    # রেফারেল ট্র্যাকিং
    if context.args and len(context.args) > 0:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user_id and user_id not in REFERRED_BY:
                REFERRED_BY[user_id] = referrer_id
                REFERRALS[referrer_id] = REFERRALS.get(referrer_id, 0) + 1
                
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎉 আপনার রেফারেল লিংকের মাধ্যমে একজন নতুন ইউজার যুক্ত হয়েছেন!\nসর্বমোট রেফার: {REFERRALS[referrer_id]}"
                    )
                except Exception as e:
                    logging.error(f"Referral Notification Error: {e}")
        except ValueError:
            pass

    keyboard = get_main_keyboard(user_id)
    await update.message.reply_text("স্বাগতম! নিচে প্রদত্ত মেনু থেকে অপশন সিলেক্ট করুন:", reply_markup=keyboard)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    ALL_USERS.add(user_id)

    # ১. ব্রডকাস্ট প্রসেসিং
    if USER_STATE.get(user_id) == "WAITING_FOR_BROADCAST" and user_id == ADMIN_ID:
        USER_STATE[user_id] = None
        
        if text == "❌ Cancel":
            await update.message.reply_text("ব্রডকাস্ট বাতিল করা হয়েছে।", reply_markup=get_main_keyboard(user_id))
            return

        success_count = 0
        failed_count = 0
        status_msg = await update.message.reply_text("মেসেজ ব্রডকাস্ট শুরু হচ্ছে...")

        for uid in list(ALL_USERS):
            try:
                await update.message.copy(chat_id=uid)
                success_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed_count += 1

        await status_msg.edit_text(
            f"✅ **ব্রডকাস্ট সম্পন্ন হয়েছে!**\n\n"
            f"মোট সফল: {success_count}\n"
            f"ব্যর্থ/ব্লকড: {failed_count}"
        )
        return

    # ২. মেনু বাটন হ্যান্ডলার
    if text == "Get Number":
        USER_STATE[user_id] = "WAITING_FOR_RANGE"
        await update.message.reply_text("দয়া করে Range আইডি বা Range Digits (যেমন: 26134) টাইপ করে পাঠান:")
        return

    elif text == "View Traffic":
        await update.message.reply_text("ট্রাফিকের তথ্য আপডেট করা হচ্ছে...")
        return

    elif text == "Refer":
        bot_username = (await context.bot.get_me()).username
        refer_link = f"https://t.me/{bot_username}?start={user_id}"
        total_refers = REFERRALS.get(user_id, 0)
        
        msg = (
            f"🔗 **আপনার ব্যক্তিগত রেফারেল লিংক:**\n`{refer_link}`\n\n"
            f"👥 **মোট সফল রেফার:** {total_refers}\n"
            f"লিংক শেয়ার করে বন্ধুদের ইনভাইট করুন!"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    elif text == "Support":
        await update.message.reply_text(f"যেকোনো সাহায্যের জন্য যোগাযোগ করুন: @{ADMIN_USERNAME}")
        return

    elif text == "Wallet":
        await update.message.reply_text("আপনার ওয়ালেট ব্যালেন্স: $0.00")
        return

    # ৩. অ্যাডমিন প্যানেল
    elif text == "AdminPanel" and user_id == ADMIN_ID:
        admin_buttons = [
            [KeyboardButton("Manage Services"), KeyboardButton("Broadcast")],
            [KeyboardButton("Withdraw"), KeyboardButton("Users Count")],
            [KeyboardButton("Back to Main Menu")]
        ]
        await update.message.reply_text("অ্যাডমিন প্যানেলে স্বাগতম:", reply_markup=ReplyKeyboardMarkup(admin_buttons, resize_keyboard=True))
        return

    elif text == "Users Count" and user_id == ADMIN_ID:
        await update.message.reply_text(f"📊 **মোট রেজিস্টার্ড ইউজার:** {len(ALL_USERS)} জন।", parse_mode="Markdown")
        return

    elif text == "Broadcast" and user_id == ADMIN_ID:
        USER_STATE[user_id] = "WAITING_FOR_BROADCAST"
        cancel_btn = ReplyKeyboardMarkup([[KeyboardButton("❌ Cancel")]], resize_keyboard=True)
        await update.message.reply_text(
            "📢 **ব্রডকাস্ট মেসেজ পাঠাবেন:**\n\n"
            "যে মেসেজটি (লেখা, ছবি বা ভিডিও) সকল ইউজারের কাছে পাঠাতে চান, তা এখানে টাইপ বা ফরোয়ার্ড করুন।",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return

    elif text == "Back to Main Menu":
        USER_STATE[user_id] = None
        await update.message.reply_text("মূল মেনু:", reply_markup=get_main_keyboard(user_id))
        return

    # ৪. Range ID
    if USER_STATE.get(user_id) == "WAITING_FOR_RANGE":
        USER_STATE[user_id] = None
        rid = text.strip()
        await update.message.reply_text("নম্বর প্রসেস করা হচ্ছে, অপেক্ষা করুন...")
        await generate_and_send_number(user_id=user_id, rid=rid, update=update)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("change_num:"):
        rid = query.data.split(":")[1]
        user_id = query.from_user.id
        await query.message.reply_text("নতুন নম্বর আনা হচ্ছে...")
        await generate_and_send_number(user_id=user_id, rid=rid, query=query)

# --- OTP POLLER ---

async def otp_poller(application: Application):
    seen_otp_ids = set()

    while True:
        try:
            res = await api_get_otps()
            if res and res.get("meta", {}).get("code") == 200:
                otps = res.get("data", {}).get("otps", [])
                
                for otp_data in otps:
                    otp_id = otp_data.get("otp_id")
                    number = otp_data.get("number")
                    raw_message = otp_data.get("message", "")

                    if otp_id not in seen_otp_ids:
                        for target_user_id, num_dict in USER_ALLOCATED_NUMBERS.items():
                            if number in num_dict:
                                country_name = num_dict.get(number, "Unknown")
                                clean_code = extract_clean_otp(raw_message)

                                user_alert_text = (
                                    f"📩 **OTP Received**\n\n"
                                    f"Country: {country_name}\n"
                                    f"Number: {number}"
                                )

                                group_alert_text = (
                                    f"📩 **New OTP Received**\n\n"
                                    f"Country: {country_name}\n"
                                    f"Number: {mask_number(number)}"
                                )

                                user_otp_keyboard = InlineKeyboardMarkup([
                                    [InlineKeyboardButton(text=f"{clean_code}", copy_text={"text": clean_code})]
                                ])

                                group_otp_keyboard = InlineKeyboardMarkup([
                                    [
                                        InlineKeyboardButton(text=f"{clean_code}", copy_text={"text": clean_code})
                                    ],
                                    [
                                        InlineKeyboardButton(text="Get Number", url=BOT_LINK),
                                        InlineKeyboardButton(text="Channel", url=CHANNEL_LINK)
                                    ]
                                ])

                                try:
                                    await application.bot.send_message(
                                        chat_id=target_user_id, 
                                        text=user_alert_text, 
                                        reply_markup=user_otp_keyboard,
                                        parse_mode="Markdown"
                                    )
                                except Exception as u_err:
                                    logging.error(f"User Send Error: {u_err}")

                                try:
                                    await application.bot.send_message(
                                        chat_id=FORWARD_GROUP_ID, 
                                        text=group_alert_text, 
                                        reply_markup=group_otp_keyboard,
                                        parse_mode="Markdown"
                                    )
                                except Exception as g_err:
                                    logging.error(f"Group Send Error: {g_err}")

                                seen_otp_ids.add(otp_id)
                                break
        except Exception as e:
            logging.error(f"Error in OTP Poller: {e}")

        await asyncio.sleep(3)

# --- MAIN ---

async def post_init(application: Application):
    asyncio.create_task(otp_poller(application))

def main():
    # Flask Server ব্যাকগ্রাউন্ড থ্রেডে স্টার্ট করা
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    print("Bot and Web Server are running...")
    app.run_polling()

if __name__ == "__main__":
    main()
