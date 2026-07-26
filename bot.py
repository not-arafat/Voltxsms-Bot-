import asyncio
import base64
import json
import logging
import re
import os
import threading
import aiohttp
from flask import Flask, jsonify
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --- FIREBASE SETUP ---
import firebase_admin
from firebase_admin import credentials, firestore

CRED_PATH = "serviceAccountKey.json"
load_dotenv()
SERVICE_ACCOUNT_KEY_JSON = os.environ.get("SERVICE_ACCOUNT_KEY_JSON")
SERVICE_ACCOUNT_KEY_B64 = os.environ.get("SERVICE_ACCOUNT_KEY_B64")

def _decode_base64_payload(value: str) -> str:
    try:
        decoded_bytes = base64.b64decode(value, validate=True)
        return decoded_bytes.decode("utf-8")
    except Exception as e:
        logging.debug(f"Base64 decode failed: {e}")
        return value


def _parse_json_payload(value: str):
    try:
        return json.loads(value)
    except Exception as e:
        logging.debug(f"JSON parse failed: {e}")
        return None


def _load_firebase_credentials_from_env(value: str, is_base64: bool = False):
    if not value:
        return None

    payload = value
    if is_base64:
        payload = _decode_base64_payload(value)

    cred_data = _parse_json_payload(payload)
    if cred_data is None and not is_base64:
        return None
    if cred_data is None and is_base64:
        cred_data = _parse_json_payload(value)

    if cred_data is None:
        return None

    try:
        cred = credentials.Certificate(cred_data)
        firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        logging.error(f"Firebase init failed from {'base64' if is_base64 else 'raw'} env: {e}")
        return None

if not firebase_admin._apps:
    db = _load_firebase_credentials_from_env(SERVICE_ACCOUNT_KEY_B64, is_base64=True)
    if db is None:
        db = _load_firebase_credentials_from_env(SERVICE_ACCOUNT_KEY_JSON, is_base64=False)

    if db is None and os.path.exists(CRED_PATH):
        cred = credentials.Certificate(CRED_PATH)
        firebase_admin.initialize_app(cred)
        db = firestore.client()

    if db is None:
        logging.warning("serviceAccountKey.json পাওয়া যায়নি এবং SERVICE_ACCOUNT_KEY_* সেট করা হয়নি! ফায়ারবেস ব্যাকআপ ছাড়া বট চলবে।")

# --- DEFAULT CONFIGURATION ---
BOT_TOKEN = "8345617098:AAF2vkdIWCr9qphXFQI1YEPb4fvp2XqFee4"
ADMIN_ID = 7503077434
API_KEY = "MM986GSKFJ1"
BASE_URL = "https://api.2oo9.cloud/MXS47FLFX0U/tnevs/@public/api"
BOT_LINK = "https://t.me/UniversaITest_Bot"

# Global System Settings Container
SETTINGS = {
    "admin_username": "not_arafat",
    "forward_group_id": -1003925783286,
    "channel_link": "https://t.me/BackUpChannnel",
    "otp_group_link": "https://t.me/mytesstgroup2",
    "numbers_per_request": 1
}

def load_settings():
    """ফায়ারবেস থেকে সেটিংস লোড করে"""
    global SETTINGS
    if db:
        doc = db.collection('settings').document('config').get()
        if doc.exists:
            SETTINGS.update(doc.to_dict())

def save_settings():
    """ফায়ারবেসে সেটিংস আপডেট করে"""
    if db:
        db.collection('settings').document('config').set(SETTINGS, merge=True)

load_settings()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

USER_STATE = {}
ADMIN_TEMP_DATA = {}           # {admin_id: {"service": ..., "country": ...}}
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
    num_str = str(num_str)
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
        [KeyboardButton("Invite"), KeyboardButton("Status")],
        [KeyboardButton("Support"), KeyboardButton("Wallet")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton("AdminPanel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# --- USER SERVICE & COUNTRY MENU BUILDERS ---

async def show_user_services_menu(message_or_query):
    if not db:
        text = "❌ ফায়ারবেস সংযোগ পাওয়া যায়নি।"
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(text)
        else:
            await message_or_query.message.edit_text(text)
        return

    services = list(db.collection('services').stream())
    if not services:
        text = "⚠️ কোনো সার্ভিস পাওয়া যায়নি।"
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(text)
        else:
            await message_or_query.message.edit_text(text)
        return

    inline_keyboard = []
    for s in services:
        s_id = s.id
        inline_keyboard.append([InlineKeyboardButton(text=f"{s_id}", callback_data=f"usr_svc:{s_id}")])

    text = "একটি সার্ভিস নির্বাচন করুন:"
    if hasattr(message_or_query, "reply_text"):
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard))
    else:
        await message_or_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard))

async def show_user_countries_menu(query, service_id: str):
    if not db:
        return

    s_doc = db.collection('services').document(service_id).get()
    countries = {}
    if s_doc.exists:
        countries = s_doc.to_dict().get("countries", {})

    if not countries:
        await query.message.edit_text(
            f"⚠️ **{service_id}**-এর জন্য কোনো কান্ট্রি পাওয়া যায়নি।",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(text="⬅️ Back to Service", callback_data="usr_svc_back")
            ]]),
            parse_mode="Markdown"
        )
        return

    inline_keyboard = []
    temp_row = []

    for c_name, r_id in countries.items():
        btn = InlineKeyboardButton(text=f"{c_name}", callback_data=f"usr_cntry:{service_id}:{r_id}")
        temp_row.append(btn)
        
        if len(temp_row) == 2:
            inline_keyboard.append(temp_row)
            temp_row = []

    if temp_row:
        inline_keyboard.append(temp_row)

    inline_keyboard.append([
        InlineKeyboardButton(text="⬅️ Back to Service", callback_data="usr_svc_back")
    ])

    text = f"**{service_id}**-এর জন্য দেশ সিলেক্ট করুন:"
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard), parse_mode="Markdown")

# --- ADMIN SERVICE & SETTINGS UI ---

async def show_manage_services_menu(message_or_query):
    if not db:
        text = "❌ ফায়ারবেস সংযোগ নেই।"
        if hasattr(message_or_query, "reply_text"):
            await message_or_query.reply_text(text)
        else:
            await message_or_query.message.edit_text(text)
        return

    services = list(db.collection('services').stream())
    inline_keyboard = []

    if services:
        for s in services:
            s_name = s.id
            inline_keyboard.append([
                InlineKeyboardButton(text=f"{s_name}", callback_data=f"adm_svc_view:{s_name}")
            ])
    
    inline_keyboard.append([
        InlineKeyboardButton(text="➕ Add New Service", callback_data="adm_svc_add")
    ])

    text = "🛠 **Manage Services**\n\nএকটি সার্ভিস নির্বাচন করুন অথবা নতুন সার্ভিস যোগ করুন:"

    if hasattr(message_or_query, "reply_text"):
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard), parse_mode="Markdown")
    else:
        await message_or_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard), parse_mode="Markdown")

async def show_manage_countries_menu(query, service_name: str):
    if not db:
        return
    
    s_doc = db.collection('services').document(service_name).get()
    countries = {}
    if s_doc.exists:
        countries = s_doc.to_dict().get("countries", {})

    inline_keyboard = []
    for c_name in countries.keys():
        inline_keyboard.append([
            InlineKeyboardButton(text=f"{c_name}", callback_data=f"adm_cntry_view:{service_name}:{c_name}")
        ])

    inline_keyboard.append([
        InlineKeyboardButton(text="➕ Add Country", callback_data=f"adm_cntry_add:{service_name}")
    ])
    inline_keyboard.append([
        InlineKeyboardButton(text="🗑 Delete Service", callback_data=f"adm_svc_delete:{service_name}"),
        InlineKeyboardButton(text="⬅️ Back", callback_data="adm_svc_back")
    ])

    text = f"⚙️ **Service:** `{service_name}`\n\nএই সার্ভিসের অধীনস্থ দেশসমূহ:"
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard), parse_mode="Markdown")

async def show_settings_menu(message_or_query):
    """অ্যাডমিন সেটিংস ড্যাশবোর্ড"""
    text = (
        "⚙️ **Bot System Settings**\n\n"
        f"👤 **Admin Username:** `@{SETTINGS['admin_username']}`\n"
        f"📢 **Forward Group ID:** `{SETTINGS['forward_group_id']}`\n"
        f"🔗 **Channel Link:** `{SETTINGS['channel_link']}`\n"
        f"🔢 **Numbers Per Request:** `{SETTINGS['numbers_per_request']}`"
    )

    buttons = [
        [InlineKeyboardButton(text="Edit Admin Username", callback_data="cfg_set:admin_username")],
        [InlineKeyboardButton(text="Edit Group ID", callback_data="cfg_set:forward_group_id")],
        [InlineKeyboardButton(text="Edit Channel Link", callback_data="cfg_set:channel_link")],
        [InlineKeyboardButton(text="Set Number/Request", callback_data="cfg_set:numbers_per_request")]
    ]

    markup = InlineKeyboardMarkup(buttons)
    if hasattr(message_or_query, "reply_text"):
        await message_or_query.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message_or_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

# --- MULTI-NUMBER GENERATOR ---

async def generate_and_send_number(user_id: int, rid: str, update: Update = None, query = None, service_id: str = ""):
    count = SETTINGS.get("numbers_per_request", 1)
    
    fetched_numbers = []
    country_name = "Unknown"

    for _ in range(count):
        res = await api_get_number(rid)
        if res and res.get("meta", {}).get("code") == 200:
            data = res.get("data", {})
            f_num = data.get("full_number")
            no_plus = data.get("no_plus_number")
            country_name = data.get("country", "Unknown")

            if user_id not in USER_ALLOCATED_NUMBERS:
                USER_ALLOCATED_NUMBERS[user_id] = {}
            USER_ALLOCATED_NUMBERS[user_id][no_plus] = country_name

            fetched_numbers.append(f_num)

    USER_LAST_RANGE[user_id] = rid

    if fetched_numbers:
        msg_text = (
            f"**{country_name}'s Numbers Assigned ({len(fetched_numbers)}):**\n"
            f"╭─────────────────╮\n"
            f"│      🌟 Waiting For OTP: \n"
            f"╰─────────────────╯"
        )

        buttons = []
        for num in fetched_numbers:
            buttons.append([InlineKeyboardButton(text=f"{num}", copy_text={"text": str(num)})])

        buttons.append([
            InlineKeyboardButton(text="🔄 Change Number", callback_data=f"change_num:{rid}:{service_id}"),
            InlineKeyboardButton(text="📩 OTP Group", url=SETTINGS["otp_group_link"])
        ])

        if service_id:
            buttons.append([InlineKeyboardButton(text="⬅️ Back to Countries", callback_data=f"usr_svc:{service_id}")])

        inline_keyboard = InlineKeyboardMarkup(buttons)

        if query:
            await query.message.edit_text(msg_text, reply_markup=inline_keyboard, parse_mode="Markdown")
        elif update and update.message:
            await update.message.reply_text(msg_text, reply_markup=inline_keyboard, parse_mode="Markdown")

    else:
        err_msg = "দুঃখিত, বর্তমানে এই রেঞ্জের নম্বর স্টকে নেই অথবা নম্বর বরাদ্দ করতে সমস্যা হয়েছে।"
        if query:
            await query.message.edit_text(
                err_msg, 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text="⬅️ Back to Countries", callback_data=f"usr_svc:{service_id}")]]) if service_id else None
            )
        elif update and update.message:
            await update.message.reply_text(err_msg)

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if db:
        user_ref = db.collection('users').document(str(user_id))
        if not user_ref.get().exists:
            user_ref.set({
                "user_id": user_id,
                "username": update.effective_user.username or "",
                "referrals": 0,
                "referred_by": None
            })
            
            if context.args and len(context.args) > 0:
                try:
                    ref_id = int(context.args[0])
                    if ref_id != user_id:
                        user_ref.update({"referred_by": ref_id})
                        ref_doc = db.collection('users').document(str(ref_id))
                        if ref_doc.get().exists:
                            curr_ref = ref_doc.get().to_dict().get("referrals", 0)
                            ref_doc.update({"referrals": curr_ref + 1})
                except ValueError:
                    pass

    keyboard = get_main_keyboard(user_id)
    await update.message.reply_text("স্বাগতম! নিচে প্রদত্ত মেনু থেকে অপশন সিলেক্ট করুন:", reply_markup=keyboard)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # SETTINGS EDIT HANDLING
    if USER_STATE.get(user_id) and USER_STATE[user_id].startswith("WAIT_CFG:") and user_id == ADMIN_ID:
        cfg_key = USER_STATE[user_id].split(":")[1]
        
        if cfg_key == "numbers_per_request":
            if not text.isdigit() or int(text) < 1:
                await update.message.reply_text("❌ দয়া করে একটি সঠিক সংখ্যা দিন (যেমন: 1, 2, 3...)।")
                return
            SETTINGS[cfg_key] = int(text)
        elif cfg_key == "forward_group_id":
            try:
                SETTINGS[cfg_key] = int(text)
            except ValueError:
                await update.message.reply_text("❌ সঠিক Group ID টাইপ করুন (যেমন: -100xxxxxxx)।")
                return
        else:
            SETTINGS[cfg_key] = text.replace("@", "")

        save_settings()
        USER_STATE[user_id] = None
        await update.message.reply_text("✅ **সেটিংস সফলভাবে আপডেট হয়েছে!**", parse_mode="Markdown")
        await show_settings_menu(update.message)
        return

    if USER_STATE.get(user_id) == "WAITING_FOR_BROADCAST" and user_id == ADMIN_ID:
        USER_STATE[user_id] = None
        if text == "❌ Cancel":
            await update.message.reply_text("ব্রডকাস্ট বাতিল করা হয়েছে।", reply_markup=get_main_keyboard(user_id))
            return
        
        if not db:
            await update.message.reply_text("Firebase যুক্ত নেই, ব্রডকাস্ট সম্ভব নয়।")
            return

        success, failed = 0, 0
        msg = await update.message.reply_text("মেসেজ ব্রডকাস্ট হচ্ছে...")
        users = db.collection('users').stream()
        
        for u in users:
            uid = int(u.id)
            try:
                await update.message.copy(chat_id=uid)
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        await msg.edit_text(f"✅ **ব্রডকাস্ট সম্পূর্ণ!**\n\nসফল: {success}\nব্যর্থ: {failed}", parse_mode="Markdown")
        return

    if USER_STATE.get(user_id) == "WAITING_FOR_NEW_SVC_NAME" and user_id == ADMIN_ID:
        USER_STATE[user_id] = None
        s_name = text
        if db:
            db.collection('services').document(s_name).set({"service_name": s_name, "countries": {}}, merge=True)
            await update.message.reply_text(f"✅ **{s_name}** সার্ভিসটি তৈরি করা হয়েছে।")
            await show_manage_services_menu(update.message)
        return

    if USER_STATE.get(user_id) == "WAITING_FOR_NEW_CNTRY_NAME" and user_id == ADMIN_ID:
        s_name = ADMIN_TEMP_DATA.get(user_id, {}).get("service")
        c_name = text
        ADMIN_TEMP_DATA[user_id]["country"] = c_name
        USER_STATE[user_id] = "WAITING_FOR_NEW_RANGE_ID"
        await update.message.reply_text(f"এবার `{s_name}` -> `{c_name}`-এর জন্য Range ID টি লিখুন:", parse_mode="Markdown")
        return

    if USER_STATE.get(user_id) == "WAITING_FOR_NEW_RANGE_ID" and user_id == ADMIN_ID:
        USER_STATE[user_id] = None
        s_name = ADMIN_TEMP_DATA.get(user_id, {}).get("service")
        c_name = ADMIN_TEMP_DATA.get(user_id, {}).get("country")
        r_id = text

        if db and s_name and c_name:
            db.collection('services').document(s_name).set({
                "countries": {c_name: r_id}
            }, merge=True)
            await update.message.reply_text(
                f"✅ **Range সফলভাবে আপডেট হয়েছে!**\n\n"
                f"📌 Service: `{s_name}`\n"
                f"🌍 Country: `{c_name}`\n"
                f"🔢 New Range: `{r_id}`",
                parse_mode="Markdown"
            )
            ADMIN_TEMP_DATA.pop(user_id, None)
            await show_manage_services_menu(update.message)
        return

    if text == "Get Number":
        if db:
            await show_user_services_menu(update.message)
            return

        USER_STATE[user_id] = "WAITING_FOR_RANGE"
        await update.message.reply_text("দয়া করে Range আইডি বা Range Digits (যেমন: 26134) টাইপ করে পাঠান:")
        return

    elif text == "View Traffic":
        traffic_text = "To see live traffic, checkout our OTP group:"
        traffic_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="OTP Group / View Traffic", url=SETTINGS["channel_link"])]
        ])
        await update.message.reply_text(traffic_text, reply_markup=traffic_keyboard)
        return

    elif text == "Invite":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        total_ref = 0
        if db:
            udoc = db.collection('users').document(str(user_id)).get()
            if udoc.exists:
                total_ref = udoc.to_dict().get("referrals", 0)

        await update.message.reply_text(
            f"🔗 **আপনার রেফারেল লিংক:**\n`{ref_link}`\n\n"
            f"👥 **মোট রেফার:** {total_ref}",
            parse_mode="Markdown"
        )
        return

    elif text == "Status":
        await update.message.reply_text("বট স্ট্যাটাস: সকল সার্ভিস সক্রিয় রয়েছে।")
        return

    elif text == "Support":
        await update.message.reply_text(f"যেকোনো সাহায্যের জন্য যোগাযোগ করুন: @{SETTINGS['admin_username']}")
        return

    elif text == "Wallet":
        await update.message.reply_text("আপনার ওয়ালেট ব্যালেন্স: $0.00")
        return

    elif text == "AdminPanel" and user_id == ADMIN_ID:
        admin_buttons = [
            [KeyboardButton("Manage Services"), KeyboardButton("Settings")],
            [KeyboardButton("Broadcast"), KeyboardButton("Users Count")],
            [KeyboardButton("Back to Main Menu")]
        ]
        await update.message.reply_text("অ্যাডমিন প্যানেলে স্বাগতম:", reply_markup=ReplyKeyboardMarkup(admin_buttons, resize_keyboard=True))
        return

    elif text == "Settings" and user_id == ADMIN_ID:
        await show_settings_menu(update.message)
        return

    elif text == "Manage Services" and user_id == ADMIN_ID:
        await show_manage_services_menu(update.message)
        return

    elif text == "Users Count" and user_id == ADMIN_ID:
        count = 0
        if db:
            count = len(list(db.collection('users').stream()))
        await update.message.reply_text(f"📊 **মোট রেজিস্টার্ড ইউজার:** {count} জন।", parse_mode="Markdown")
        return

    elif text == "Broadcast" and user_id == ADMIN_ID:
        USER_STATE[user_id] = "WAITING_FOR_BROADCAST"
        cancel_btn = ReplyKeyboardMarkup([[KeyboardButton("❌ Cancel")]], resize_keyboard=True)
        await update.message.reply_text("📢 **ব্রডকাস্ট মেসেজটি লিখুন বা ফরওয়ার্ড করুন:**", reply_markup=cancel_btn)
        return

    elif text == "Back to Main Menu":
        USER_STATE[user_id] = None
        await update.message.reply_text("মূল মেনু:", reply_markup=get_main_keyboard(user_id))
        return

    if USER_STATE.get(user_id) == "WAITING_FOR_RANGE":
        USER_STATE[user_id] = None
        rid = text.strip()
        await update.message.reply_text("নম্বর প্রসেস করা হচ্ছে, অপেক্ষা করুন...")
        await generate_and_send_number(user_id=user_id, rid=rid, update=update)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("cfg_set:") and user_id == ADMIN_ID:
        cfg_key = data.split(":")[1]
        if cfg_key == "numbers_per_request":
            buttons = [
                [InlineKeyboardButton(text="1", callback_data="num_req:1"), InlineKeyboardButton(text="2", callback_data="num_req:2")],
                [InlineKeyboardButton(text="3", callback_data="num_req:3"), InlineKeyboardButton(text="4", callback_data="num_req:4")],
                [InlineKeyboardButton(text="5", callback_data="num_req:5"), InlineKeyboardButton(text="6", callback_data="num_req:6")]
            ]
            await query.message.edit_text("প্রতি রিকোয়েস্টে কয়টি নম্বর চান সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(buttons))
            return

        USER_STATE[user_id] = f"WAIT_CFG:{cfg_key}"
        await query.message.reply_text(f"নতুন **{cfg_key}** এর মান লিখে মেসেজ দিন:")

    elif data.startswith("num_req:") and user_id == ADMIN_ID:
        num_val = int(data.split(":")[1])
        SETTINGS["numbers_per_request"] = num_val
        save_settings()
        await query.message.reply_text(f"✅ **Number per request {num_val} সেটিং নিশ্চিত করা হয়েছে!**")
        await show_settings_menu(query)

    elif data.startswith("change_num:"):
        parts = data.split(":")
        rid = parts[1]
        service_id = parts[2] if len(parts) > 2 else ""
        
        await query.message.edit_text("⏳ **Number Changing...**", parse_mode="Markdown")
        await generate_and_send_number(user_id=user_id, rid=rid, query=query, service_id=service_id)

    # --- USER SERVICE SELECTION HANDLERS ---
    elif data.startswith("usr_svc:"):
        s_id = data.split(":")[1]
        await show_user_countries_menu(query, s_id)

    elif data == "usr_svc_back":
        await show_user_services_menu(query)

    elif data.startswith("usr_cntry:"):
        parts = data.split(":")
        s_id = parts[1]
        rid = parts[2]
        
        await query.message.edit_text("⏳ **Number Generating...**", parse_mode="Markdown")
        await generate_and_send_number(user_id=user_id, rid=rid, query=query, service_id=s_id)

    # --- ADMIN MANAGE SERVICES CALLBACKS ---
    elif data == "adm_svc_add" and user_id == ADMIN_ID:
        USER_STATE[user_id] = "WAITING_FOR_NEW_SVC_NAME"
        await query.message.reply_text("➕ নতুন **Service Name** লিখুন (যেমন: Facebook):", parse_mode="Markdown")

    elif data.startswith("adm_svc_view:") and user_id == ADMIN_ID:
        s_name = data.split(":")[1]
        await show_manage_countries_menu(query, s_name)

    elif data.startswith("adm_svc_delete:") and user_id == ADMIN_ID:
        s_name = data.split(":")[1]
        if db:
            db.collection('services').document(s_name).delete()
            await query.message.reply_text(f"🗑 **{s_name}** সার্ভিসটি ডিলিট করা হয়েছে।")
            await show_manage_services_menu(query)

    elif data == "adm_svc_back" and user_id == ADMIN_ID:
        await show_manage_services_menu(query)

    elif data.startswith("adm_cntry_add:") and user_id == ADMIN_ID:
        s_name = data.split(":")[1]
        ADMIN_TEMP_DATA[user_id] = {"service": s_name}
        USER_STATE[user_id] = "WAITING_FOR_NEW_CNTRY_NAME"
        await query.message.reply_text(f"➕ **{s_name}**-এর জন্য দেশটির নাম লিখুন (যেমন: Bangladesh):", parse_mode="Markdown")

    elif data.startswith("adm_cntry_view:") and user_id == ADMIN_ID:
        _, s_name, c_name = data.split(":")
        if db:
            s_doc = db.collection('services').document(s_name).get()
            current_range = "Not Found"
            if s_doc.exists:
                current_range = s_doc.to_dict().get("countries", {}).get(c_name, "N/A")

            info_text = (
                f"📌 **Service:** `{s_name}`\n"
                f"🌍 **Country:** `{c_name}`\n"
                f"🔢 **Current Range ID:** `{current_range}`"
            )

            buttons = [
                [InlineKeyboardButton(text="✏️ Edit Range", callback_data=f"adm_range_edit:{s_name}:{c_name}")],
                [
                    InlineKeyboardButton(text="🗑 Delete Country", callback_data=f"adm_cntry_del:{s_name}:{c_name}"),
                    InlineKeyboardButton(text="⬅️ Back", callback_data=f"adm_svc_view:{s_name}")
                ]
            ]

            await query.message.edit_text(info_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data.startswith("adm_range_edit:") and user_id == ADMIN_ID:
        _, s_name, c_name = data.split(":")
        ADMIN_TEMP_DATA[user_id] = {"service": s_name, "country": c_name}
        USER_STATE[user_id] = "WAITING_FOR_NEW_RANGE_ID"
        await query.message.reply_text(f"🔢 **{s_name}** -> **{c_name}**-এর জন্য নতুন Range ID টি পাঠান:")

    elif data.startswith("adm_cntry_del:") and user_id == ADMIN_ID:
        _, s_name, c_name = data.split(":")
        if db:
            s_doc = db.collection('services').document(s_name).get()
            if s_doc.exists:
                countries = s_doc.to_dict().get("countries", {})
                countries.pop(c_name, None)
                db.collection('services').document(s_name).update({"countries": countries})
                await query.message.reply_text(f"🗑 **{c_name}** ডিলিট করা হয়েছে।")
                await show_manage_countries_menu(query, s_name)

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
                            if number in num_dict or str(number) in num_dict:
                                matched_key = number if number in num_dict else str(number)
                                country_name = num_dict.get(matched_key, "Unknown")
                                clean_code = extract_clean_otp(raw_message)

                                user_alert_text = (
                                    f"📩 **OTP Received**\n\n"
                                    f"Country: {country_name}\n"
                                    f"Number: {matched_key}"
                                )

                                group_alert_text = (
                                    f"📩 **New OTP Received**\n\n"
                                    f"Country: {country_name}\n"
                                    f"Number: {mask_number(matched_key)}"
                                )

                                user_otp_keyboard = InlineKeyboardMarkup([
                                    [InlineKeyboardButton(text=f"{clean_code}", copy_text={"text": clean_code})]
                                ])

                                group_otp_keyboard = InlineKeyboardMarkup([
                                    [InlineKeyboardButton(text=f"{clean_code}", copy_text={"text": clean_code})],
                                    [
                                        InlineKeyboardButton(text="Get Number", url=BOT_LINK),
                                        InlineKeyboardButton(text="Channel", url=SETTINGS["channel_link"])
                                    ]
                                ])

                                try:
                                    await application.bot.send_message(
                                        chat_id=target_user_id, 
                                        text=user_alert_text, 
                                        reply_markup=user_otp_keyboard
                                    )
                                except Exception as u_err:
                                    logging.error(f"User Send Error: {u_err}")

                                try:
                                    await application.bot.send_message(
                                        chat_id=SETTINGS["forward_group_id"], 
                                        text=group_alert_text, 
                                        reply_markup=group_otp_keyboard
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

flask_app = Flask(__name__)
app = flask_app

@flask_app.route("/")
def index():
    return "OK - Telegram bot web service is alive."

@flask_app.route("/health")
def health():
    return jsonify(status="ok", bot="running")

bot_thread = None
bot_thread_lock = threading.Lock()
bot_started = False


def create_telegram_app():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    return application


def run_telegram_bot():
    try:
        application = create_telegram_app()
        print("Bot is running... Telegram polling starting")
        application.run_polling()
    except Exception as e:
        logging.exception(f"Telegram polling failed: {e}")


def start_bot_in_thread():
    global bot_thread, bot_started
    with bot_thread_lock:
        if bot_started:
            print("Telegram bot thread already started")
            return
        print("Starting Telegram bot thread...")
        bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
        bot_thread.start()
        bot_started = True
        print("Telegram bot thread started")


@flask_app.before_request
def ensure_bot_running():
    start_bot_in_thread()

# Run bot thread on module import so Render starts polling immediately.
start_bot_in_thread()

if __name__ == "__main__":
    start_bot_in_thread()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)