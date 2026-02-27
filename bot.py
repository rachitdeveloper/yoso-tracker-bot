import os
import re
import time
import requests
import asyncio
from datetime import datetime
from typing import Dict, Set, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import json
import threading

# Configuration
TELEGRAM_BOT_TOKEN = "8152370701:AAHrmRqybN0h74JNnX_Kslu-QMLuFBncatc"  # Apna bot token yahan dale
CHECK_INTERVAL = 20  # Har minute (60 seconds)
PROXY_URL = 'http://b80EdMP2FNF9ecn:D9GSAt4lryHF7sJ@82.41.251.44:43253'
PROXIES = {
    'http': PROXY_URL,
    'https': PROXY_URL
}

# Data storage
user_markets: Dict[int, Set[str]] = {}  # {user_id: {market_addresses}}
last_activity_ids: Dict[str, Set[str]] = {}  # {market_address: {activity_ids}}
market_titles: Dict[str, str] = {}  # {market_address: "Market Title"}

class YOSOTracker:
    def __init__(self):
        self.base_url = "https://app.yoso.fun/api/markets"
        self.headers = {
            'sec-ch-ua-platform': '"Windows"',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            'sec-ch-ua-mobile': '?0'
        }
    
    def extract_address_from_url(self, url: str) -> str:
        """URL se market address extract karta hai"""
        pattern = r'(?:markets/|0x)([a-fA-F0-9]{40})'
        match = re.search(pattern, url)
        if match:
            address = match.group(1) if match.group(0).startswith('0x') else match.group(1)
            return f"0x{address}" if not address.startswith('0x') else address
        return None
    
    def get_activities(self, market_address: str) -> dict:
        """Market activities fetch karta hai"""
        try:
            url = f"{self.base_url}/{market_address}/activities"
            params = {'limit': 30, 'offset': 0, 'aggregate': 'true'}
            headers = self.headers.copy()
            headers['Referer'] = f'https://app.yoso.fun/markets/{market_address}'
            
            response = requests.get(url, headers=headers, params=params, proxies=PROXIES, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching activities for {market_address}: {e}")
            return None

    def get_market_title(self, market_address: str) -> str:
        """Market page se title fetch karta hai"""
        try:
            url = f"https://app.yoso.fun/markets/{market_address}"
            response = requests.get(url, headers=self.headers, proxies=PROXIES, timeout=10)
            if response.status_code == 200:
                match = re.search(r'<title>(.*?)</title>', response.text, re.IGNORECASE)
                if match:
                    title = match.group(1).strip()
                    return title.replace(" | Yoso", "")
            return None
        except Exception as e:
            print(f"Error fetching title for {market_address}: {e}")
            return None

    def format_amount(self, amount_usdc: str) -> str:
        """USDC amount ko readable format mein convert karta hai"""
        try:
            amount = float(amount_usdc) / 1_000_000  # Convert from micro USDC
            return f"${amount:.2f}"
        except:
            return "$0.00"
    
    def format_activity(self, activity: dict) -> str:
        """Activity ko readable message format mein convert karta hai"""
        user = activity['user']['id'][:6] + "..." + activity['user']['id'][-4:]
        trade_type = "🟢 BUY" if activity['type'] == 'TRADE_BUY' else "🔴 SELL"
        outcome = f"Outcome {activity['outcome']}"
        amount = self.format_amount(activity['amountUSDC'])
        price = f"${float(activity['price']):.4f}"
        
        timestamp = datetime.fromtimestamp(int(activity['timestamp'])).strftime('%H:%M:%S')
        
        message = f"""
{trade_type} Alert! 🚨

👤 Trader: `{user}`
💰 Amount: **{amount}**
📊 Outcome: {outcome}
💵 Price: {price}
⏰ Time: {timestamp}

[View Transaction](https://basescan.org/tx/{activity['txHash']})
"""
        return message

# Global application reference
app_instance = None

# Bot Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user_id = update.effective_user.id
    
    if user_id not in user_markets:
        user_markets[user_id] = set()
    
    welcome_message = """
🎯 **YOSO Market Tracker Bot**

Use this bot to track YOSO markets!

**Commands:**
/market - Add a new market
/list - View your tracked markets
/remove - Stop tracking a market
/help - Help and instructions

The bot checks markets every minute and sends instant trade notifications! 🔔
"""
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def add_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Market add karne ka command"""
    user_id = update.effective_user.id
    
    if user_id not in user_markets:
        user_markets[user_id] = set()
    
    message = """
📝 **Add Market**

Please send the market URL or address:

**Examples:**
- `https://app.yoso.fun/markets/0xd47c6c671bf0ffec0f623f18c2e0d88393dc0c6b`
- `0xd47c6c671bf0ffec0f623f18c2e0d88393dc0c6b`

Send the URL or address in your next message 👇
"""
    await update.message.reply_text(message, parse_mode='Markdown')
    context.user_data['awaiting_market'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User messages handle karta hai"""
    user_id = update.effective_user.id
    
    if context.user_data.get('awaiting_market'):
        tracker = YOSOTracker()
        text = update.message.text.strip()
        
        # Address extract karo
        market_address = tracker.extract_address_from_url(text)
        
        if not market_address:
            await update.message.reply_text(
                "❌ Invalid URL or address! Please try again.\n\n"
                "Format: `https://app.yoso.fun/markets/0x...` or `0x...`",
                parse_mode='Markdown'
            )
            return
        
        # Check if market exists
        await update.message.reply_text("⏳ Verifying market...")
        data = tracker.get_activities(market_address)
        
        if not data or 'activities' not in data:
            await update.message.reply_text(
                f"❌ Market not found!\n\nAddress: `{market_address}`\n\n"
                "Please enter a valid market URL or address.",
                parse_mode='Markdown'
            )
            return
        
        # Add market
        if user_id not in user_markets:
            user_markets[user_id] = set()
        
        user_markets[user_id].add(market_address)
        
        # Initialize last activity tracking
        # Initialize last activity tracking
        if market_address not in last_activity_ids:
            last_activity_ids[market_address] = {act['id'] for act in data['activities']}
        
        # Fetch and store market title
        title = tracker.get_market_title(market_address)
        market_name = title if title else market_address
        market_titles[market_address] = market_name

        await update.message.reply_text(
            f"✅ **Market Successfully Added!**\n\n"
            f"Market: **{market_name}**\n"
            f"Address: `{market_address}`\n\n"
            f"🔔 You will now receive notifications for trades in this market!\n"
            f"📊 Total tracked markets: {len(user_markets[user_id])}",
            parse_mode='Markdown'
        )
        
        context.user_data['awaiting_market'] = False

async def list_markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ke tracked markets dikhata hai"""
    user_id = update.effective_user.id
    
    if user_id not in user_markets or not user_markets[user_id]:
        await update.message.reply_text(
            "📋 You haven't tracked any markets yet!\n\n"
            "Use /market command to add markets."
        )
        return
    
    message = "📊 **Your Tracked Markets:**\n\n"
    for idx, market in enumerate(user_markets[user_id], 1):
        market_name = market_titles.get(market, market[:6] + "..." + market[-4:])
        message += f"{idx}. **{market_name}**\n"
        message += f"   [View Market](https://app.yoso.fun/markets/{market})\n\n"
    
    message += f"\n🔔 Total: {len(user_markets[user_id])} markets"
    
    await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)

async def remove_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Market remove karne ka command"""
    user_id = update.effective_user.id
    
    if user_id not in user_markets or not user_markets[user_id]:
        await update.message.reply_text("❌ You don't have any tracked markets!")
        return
    
    # Create inline keyboard with markets
    keyboard = []
    for market in user_markets[user_id]:
        short_addr = market[:10] + "..." + market[-8:]
        keyboard.append([InlineKeyboardButton(
            short_addr, 
            callback_data=f"remove_{market}"
        )])
    
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🗑️ **Remove Market**\n\nWhich market would you like to remove?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline button callbacks handle karta hai"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "cancel":
        await query.edit_message_text("❌ Cancelled!")
        return
    
    if data.startswith("remove_"):
        market = data.replace("remove_", "")
        
        if user_id in user_markets and market in user_markets[user_id]:
            user_markets[user_id].remove(market)
            short_addr = market[:10] + "..." + market[-8:]
            await query.edit_message_text(
                f"✅ Market removed successfully!\n\n`{short_addr}`",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Market not found!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
📚 **YOSO Tracker Bot - Help**

**Commands:**
/start - Start the bot
/market - Track a new market
/list - View tracked markets
/remove - Stop tracking a market
/help - This help message

**Features:**
✅ Track multiple markets
✅ Real-time trade notifications
✅ Automatic checks every minute
✅ Buy/Sell alerts with full details

**How to add a market?**
1. Use the /market command
2. Send the market URL or address
3. The bot will verify and start tracking!

**Support:** @YourSupportChannel
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

# Background task for checking activities
async def check_markets_once(application):
    """Ek baar markets check karta hai"""
    tracker = YOSOTracker()
    
    # Get all unique markets being tracked
    all_markets = set()
    for markets in user_markets.values():
        all_markets.update(markets)
    
    if not all_markets:
        return
    
    print(f"🔍 Checking {len(all_markets)} markets...")
    
    for market_address in all_markets:
        try:
            data = tracker.get_activities(market_address)
            
            if not data or 'activities' not in data:
                continue
            
            # Get current activity IDs
            current_activities = {act['id']: act for act in data['activities']}
            
            # Initialize if first time
            if market_address not in last_activity_ids:
                last_activity_ids[market_address] = set(current_activities.keys())
                continue
            
            # Find new activities
            new_activity_ids = set(current_activities.keys()) - last_activity_ids[market_address]
            
            if new_activity_ids:
                print(f"🆕 Found {len(new_activity_ids)} new activities for {market_address[:10]}...")
                
                # Send notifications to all users tracking this market
                for user_id, markets in user_markets.items():
                    if market_address in markets:
                        for activity_id in new_activity_ids:
                            activity = current_activities[activity_id]
                            message = tracker.format_activity(activity)
                            
                            
                            market_name = market_titles.get(market_address, market_address[:6] + "..." + market_address[-4:])
                            final_message = f"🎯 Market: **{market_name}**\n{message}"
                            
                            try:
                                await application.bot.send_message(
                                    chat_id=user_id,
                                    text=final_message,
                                    parse_mode='Markdown',
                                    disable_web_page_preview=True
                                )
                            except Exception as e:
                                print(f"Error sending to user {user_id}: {e}")
                
                # Update last seen activities
                last_activity_ids[market_address] = set(current_activities.keys())
        
        except Exception as e:
            print(f"Error checking market {market_address}: {e}")
        
        # Small delay between markets
        await asyncio.sleep(1)

async def background_checker(application):
    """Background loop jo continuous markets check karta hai"""
    print(f"✅ Background checker started! Checking every {CHECK_INTERVAL} seconds")
    
    while True:
        try:
            await check_markets_once(application)
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Error in background checker: {e}")
            await asyncio.sleep(10)  # Error pe 10 sec wait karo

async def post_init(application: Application):
    """Bot start hone ke baad background task start karta hai"""
    asyncio.create_task(background_checker(application))

def main():
    """Main function"""
    print("🚀 YOSO Market Tracker Bot Starting...")
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("market", add_market))
    application.add_handler(CommandHandler("list", list_markets))
    application.add_handler(CommandHandler("remove", remove_market))
    application.add_handler(CommandHandler("help", help_command))
    
    # Add callback handler for buttons
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Set post_init to start background task
    application.post_init = post_init
    
    print("✅ Bot is running!")
    print(f"📊 Checking markets every {CHECK_INTERVAL} seconds")
    
    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

