
import os
import time
import requests
import random
from datetime import datetime, timedelta
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from replit import db

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
BSCSCAN_API = os.getenv("BSCSCAN_API")
WALLET = os.getenv("WALLET")
GROUP_ID = int(os.getenv("GROUP_ID"))

# Define payment plans
PLANS = {
    "15": {"amount": 5, "days": 15},
    "30": {"amount": 10, "days": 30}
}

# User storage (in memory for now)
paid_users = {}

# CAPTCHA system
CAPTCHA_EMOJIS = ["🐱", "🐶", "🦊", "🐻", "🐼", "🐸", "🐨", "🦁", "🐯", "🐰"]
CAPTCHA_VALIDITY_HOURS = 24

def is_wallet_already_used(wallet_address: str, telegram_id: int) -> bool:
    """Check if wallet has been used by a different Telegram user"""
    wallet_key = f"wallet_{wallet_address.lower()}"
    
    if wallet_key in db:
        stored_data = db[wallet_key]
        # If wallet was used by different telegram_id, reject
        if stored_data["telegram_id"] != telegram_id:
            return True
    
    return False

def is_wallet_used_before(wallet_address: str) -> bool:
    """Check if wallet has been used for any payment before"""
    wallet_key = f"wallet_{wallet_address.lower()}"
    
    if wallet_key in db:
        wallet_data = db[wallet_key]
        # If wallet was used and invite was already given, block it
        if wallet_data.get("paid", False) and wallet_data.get("invite_given", False):
            return True
    
    return False

def is_user_verified(user_id: int) -> bool:
    """Check if user has passed CAPTCHA recently or is already paid"""
    user_key = f"user_{user_id}"
    
    # Check if user is already paid (skip CAPTCHA for paid users)
    if user_key in db:
        user_data = db[user_key]
        if user_data.get("paid", False):
            return True
    
    # Check CAPTCHA verification
    captcha_key = f"captcha_{user_id}"
    if captcha_key in db:
        captcha_data = db[captcha_key]
        verified_time = datetime.fromisoformat(captcha_data["verified_at"])
        
        # Check if verification is still valid (24 hours)
        if datetime.now() - verified_time < timedelta(hours=CAPTCHA_VALIDITY_HOURS):
            return True
    
    return False

def is_first_time_user(user_id: int) -> bool:
    """Check if this is user's first interaction with the bot"""
    user_key = f"user_{user_id}"
    captcha_key = f"captcha_{user_id}"
    first_visit_key = f"first_visit_{user_id}"
    
    # If user has any record (paid, captcha, or first visit), they're not first-time
    if user_key in db or captcha_key in db or first_visit_key in db:
        return False
    
    return True

def mark_user_visited(user_id: int):
    """Mark that user has visited before"""
    first_visit_key = f"first_visit_{user_id}"
    visit_data = {
        "first_visit": datetime.now().isoformat(),
        "user_id": user_id
    }
    db[first_visit_key] = visit_data

def store_captcha_verification(user_id: int):
    """Store CAPTCHA verification timestamp"""
    captcha_key = f"captcha_{user_id}"
    captcha_data = {
        "verified_at": datetime.now().isoformat(),
        "user_id": user_id
    }
    db[captcha_key] = captcha_data

def generate_captcha() -> tuple:
    """Generate CAPTCHA with correct emoji and 3 distractors"""
    correct_emoji = random.choice(CAPTCHA_EMOJIS)
    all_emojis = CAPTCHA_EMOJIS.copy()
    all_emojis.remove(correct_emoji)
    distractors = random.sample(all_emojis, 3)
    
    # Shuffle options
    options = [correct_emoji] + distractors
    random.shuffle(options)
    
    return correct_emoji, options

def store_wallet_mapping(wallet_address: str, telegram_id: int, plan_days: int):
    """Store wallet to Telegram ID mapping with invite flag"""
    wallet_key = f"wallet_{wallet_address.lower()}"
    user_key = f"user_{telegram_id}"
    
    wallet_data = {
        "telegram_id": telegram_id,
        "paid": True,
        "invite_given": True,  # Mark that invite has been given
        "joined_at": datetime.now().isoformat(),
        "plan_days": plan_days
    }
    
    user_data = {
        "wallet_address": wallet_address.lower(),
        "paid": True,
        "invite_given": True,  # Mark that invite has been given
        "joined_at": datetime.now().isoformat(),
        "plan_days": plan_days
    }
    
    db[wallet_key] = wallet_data
    db[user_key] = user_data

def check_payment(address: str, amount: float) -> bool:
    # USDT contract address on BSC
    USDT_CONTRACT = "0x55d398326f99059ff775485246999027b3197955"
    
    # Check for USDT token transfers to our wallet
    url = f"https://api.bscscan.com/api?module=account&action=tokentx&contractaddress={USDT_CONTRACT}&address={WALLET}&page=1&offset=20&sort=desc&apikey={BSCSCAN_API}"
    
    response = requests.get(url)
    if response.status_code != 200:
        return False
    
    data = response.json()
    if data["status"] != "1":
        return False
    
    # Check recent USDT transfers
    for tx in data["result"]:
        # Check if transfer is TO our wallet and FROM the user's address
        if (tx["to"].lower() == WALLET.lower() and 
            tx["from"].lower() == address.lower()):
            
            # USDT has 18 decimals, convert value
            usdt_amount = float(tx["value"]) / 1e18
            
            # Check if amount is sufficient (with small tolerance for fees)
            if usdt_amount >= amount * 0.95:  # 5% tolerance
                return True
    
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # First-time users get immediate access (no CAPTCHA)
    if is_first_time_user(user.id):
        mark_user_visited(user.id)
        await update.message.reply_text(
            "🎉 Welcome to the Premium Group Access Bot!\n\n" +
            "Send /buy15 for 15 days ($5)\nSend /buy30 for 30 days ($10)"
        )
        return
    
    # Returning users need CAPTCHA verification (anti-spam)
    if not is_user_verified(user.id):
        await send_captcha(update, context)
        return
    
    # User is verified, proceed with normal flow
    await update.message.reply_text(
        "Welcome back to the Premium Group Access Bot!\n\n" +
        "Send /buy15 for 15 days ($5)\nSend /buy30 for 30 days ($10)"
    )

async def send_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send CAPTCHA challenge to user"""
    user = update.effective_user
    correct_emoji, options = generate_captcha()
    
    # Store correct answer temporarily
    context.user_data['captcha_answer'] = correct_emoji
    
    # Create inline keyboard with emoji options
    keyboard = []
    row = []
    for i, emoji in enumerate(options):
        row.append(InlineKeyboardButton(emoji, callback_data=f"captcha_{emoji}"))
        if len(row) == 2:  # 2 emojis per row
            keyboard.append(row)
            row = []
    if row:  # Add remaining emojis
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🤖 **Anti-Spam Verification**\n\n"
        f"Click the {correct_emoji} emoji to continue:",
        reply_markup=reply_markup
    )

async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CAPTCHA button clicks"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    clicked_emoji = query.data.replace("captcha_", "")
    correct_emoji = context.user_data.get('captcha_answer')
    
    if clicked_emoji == correct_emoji:
        # CAPTCHA passed
        store_captcha_verification(user.id)
        
        await query.edit_message_text(
            f"✅ Verification successful!\n\n"
            f"Welcome to the Premium Group Access Bot!\n\n"
            f"Send /buy15 for 15 days ($5)\nSend /buy30 for 30 days ($10)"
        )
        
        # Clear captcha answer from memory
        context.user_data.pop('captcha_answer', None)
    else:
        # CAPTCHA failed
        await query.edit_message_text(
            f"❌ Wrong selection! Please try again.\n\n"
            f"Send /start to get a new verification."
        )
        
        # Clear captcha answer from memory
        context.user_data.pop('captcha_answer', None)

async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is verified before allowing purchase
    if not is_user_verified(user.id):
        await update.message.reply_text(
            "🤖 Please complete verification first. Send /start to begin."
        )
        return
    
    plan = update.message.text.replace("/buy", "")
    if plan not in PLANS:
        await update.message.reply_text("Invalid plan.")
        return
    
    await update.message.reply_text(
        f"Send ${PLANS[plan]['amount']} USDT (BNB Chain) to this address:\n\n"
        f"{WALLET}\n\nThen type /confirm <your wallet address>"
    )

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Check if user is verified before allowing confirmation
    if not is_user_verified(user.id):
        await update.message.reply_text(
            "🤖 Please complete verification first. Send /start to begin."
        )
        return
    
    try:
        address = context.args[0]
    except IndexError:
        await update.message.reply_text("❌ Please enter your wallet address like this:\n/confirm 0x123...")
        return
    
    # Check if wallet has been used before (by anyone)
    if is_wallet_used_before(address):
        await update.message.reply_text(
            "✅ You have already joined the group with this wallet. If you want access for another person, please use a different wallet and make a new payment."
        )
        return
    
    # Check if wallet has already been used by another user (additional safety check)
    if is_wallet_already_used(address, user.id):
        await update.message.reply_text(
            "❌ This wallet has already been used for access. Each payment must come from a unique wallet.\n\n"
            "Please use a different wallet address for your payment."
        )
        return
    
    for plan in PLANS:
        if check_payment(address, PLANS[plan]['amount']):
            expire = datetime.now() + timedelta(days=PLANS[plan]['days'])
            paid_users[user.id] = expire
            
            # Store wallet-to-telegram mapping
            store_wallet_mapping(address, user.id, PLANS[plan]['days'])
            
            await context.bot.send_message(GROUP_ID, f"✅ {user.full_name} joined for {PLANS[plan]['days']} days.")
            
            # Generate invite link instead of directly adding user
            try:
                invite_link = await context.bot.create_chat_invite_link(
                    chat_id=GROUP_ID,
                    member_limit=1,
                    expire_date=expire
                )
                await update.message.reply_text(
                    f"✅ Payment confirmed! Click this link to join the group:\n{invite_link.invite_link}\n\n"
                    f"Access expires in {PLANS[plan]['days']} days."
                )
            except Exception as e:
                await update.message.reply_text(
                    f"✅ Payment confirmed! You have {PLANS[plan]['days']} days access. "
                    f"Please contact admin to get the group invite link."
                )
            return
    await update.message.reply_text("❌ Payment not found. Try again later.")

async def wallet_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if a wallet has been used before"""
    user = update.effective_user
    
    # Check if user is verified before allowing wallet status check
    if not is_user_verified(user.id):
        await update.message.reply_text(
            "🤖 Please complete verification first. Send /start to begin."
        )
        return
    
    try:
        address = context.args[0]
    except IndexError:
        await update.message.reply_text("❌ Please enter wallet address like this:\n/wallet_status 0x123...")
        return
    
    wallet_key = f"wallet_{address.lower()}"
    
    if wallet_key in db:
        wallet_data = db[wallet_key]
        if wallet_data["telegram_id"] == user.id:
            invite_status = "Yes" if wallet_data.get("invite_given", False) else "No"
            await update.message.reply_text(
                f"✅ This wallet is registered to your account.\n"
                f"Joined: {wallet_data['joined_at']}\n"
                f"Plan: {wallet_data['plan_days']} days\n"
                f"Invite Given: {invite_status}"
            )
        else:
            await update.message.reply_text("❌ This wallet is already used. Each wallet can only be used once for access.")
    else:
        await update.message.reply_text("✅ This wallet is available for use.")

async def check_expiry(bot: Bot):
    while True:
        now = datetime.now()
        for user_id in list(paid_users.keys()):
            if paid_users[user_id] < now:
                await bot.send_message(GROUP_ID, f"⏳ Removing expired user: {user_id}")
                try:
                    await bot.ban_chat_member(GROUP_ID, user_id)
                    await bot.unban_chat_member(GROUP_ID, user_id)
                except:
                    pass
                del paid_users[user_id]
        time.sleep(3600)  # Check every hour

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy15", buy_plan))
    app.add_handler(CommandHandler("buy30", buy_plan))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("wallet_status", wallet_status))
    app.add_handler(CallbackQueryHandler(handle_captcha, pattern="^captcha_"))
    
    bot = Bot(BOT_TOKEN)
    # Note: app.create_task is not the correct way to run background tasks
    # For now, we'll just run the polling
    app.run_polling()
