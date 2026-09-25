import os
import json
import telebot
from price_checker import PriceChecker
import yfinance as yf

# Load config to get token if not in env
bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
if not bot_token:
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            bot_token = config.get('email_settings', {}).get('telegram_bot_token')
    except Exception:
        pass

# Fallback token if user hardcoded it previously
if not bot_token:
    bot_token = "8230283680:AAGioiq4Iapwohcp9HHgrYDpFrFBNHUC3e8" # Token mentioned in context

bot = telebot.TeleBot(bot_token)
print("Telegram Bot is running...")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Welcome to the Commodity Analysis Bot! 📈\n\n"
                          "Use the /near command to see which stocks/commodities are nearing a STRONG BUY or STRONG SELL confluence.")

@bot.message_handler(commands=['near'])
def get_near_alerts(message):
    bot.reply_to(message, "🔍 Scanning the market for near setups... This may take a few seconds.")
    
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            
        checker = PriceChecker(config)
        
        near_buy = []
        near_sell = []
        
        # Scan stocks
        for symbol, name in checker.stocks.items():
            levels = checker.get_support_resistance_levels(symbol, current_price=100)
            if not levels or 'RSI_14' not in levels or levels['RSI_14'] is None:
                continue
                
            rsi = levels['RSI_14']
            
            if rsi < 45: # Approaching 30
                near_buy.append(f"• *{name}*: RSI {rsi:.2f}")
            elif rsi > 65: # Approaching 70
                near_sell.append(f"• *{name}*: RSI {rsi:.2f}")
                
        # Format response
        response = "*🚨 Near Alert Setups 🚨*\n\n"
        
        response += "*🟢 Nearing STRONG BUY (Oversold):*\n"
        if near_buy:
            response += "\n".join(near_buy[:15]) + "\n"
        else:
            response += "None currently.\n"
            
        response += "\n*🔴 Nearing STRONG SELL (Overbought):*\n"
        if near_sell:
            response += "\n".join(near_sell[:15]) + "\n"
        else:
            response += "None currently.\n"
            
        bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"An error occurred while scanning: {e}")

if __name__ == '__main__':
    bot.infinity_polling()
