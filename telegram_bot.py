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
                          "Commands:\n"
                          "• /circuits - View 30-day MCX circuit limit hits & next-day price movements\n"
                          "• /near - Scan assets nearing oversold/overbought setups")

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
        for symbol, data in checker.stocks.items():
            stock_name = data.get('name', symbol) if isinstance(data, dict) else data
            levels = checker.get_support_resistance_levels(symbol, current_price=100)
            if not levels or 'RSI_14' not in levels or levels['RSI_14'] is None:
                continue
                
            rsi = levels['RSI_14']
            
            if rsi < 45: # Approaching 30
                near_buy.append(f"• *{stock_name}*: RSI {rsi:.2f}")
            elif rsi > 65: # Approaching 70
                near_sell.append(f"• *{stock_name}*: RSI {rsi:.2f}")
                
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
        
@bot.message_handler(commands=['circuits', 'circuit'])
def get_circuits_summary(message):
    try:
        data_path = 'web/circuits_history_30d.json'
        if os.path.exists(data_path):
            with open(data_path, 'r') as f:
                data = json.load(f)
        else:
            with open('config.json', 'r') as f:
                config = json.load(f)
            checker = PriceChecker(config)
            data = checker.generate_circuits_history_30d()

        summary = data.get('summary', {})
        events = data.get('events', [])

        resp = "⚡ *MCX Circuit Limits & Next-Day Moves (Past 30 Days)*\n\n"
        resp += f"• *Total Events:* {summary.get('total_events', len(events))}\n"
        resp += f"• *Intraday Reversal Rate:* {summary.get('reversal_win_rate_pct', 0)}%\n"
        resp += f"• *Next-Day Reversal Rate:* {summary.get('next_day_win_rate_pct', 0)}%\n"
        resp += f"• *Avg Next-Day Move:* ±{summary.get('avg_next_day_move_pct', 0)}%\n\n"
        resp += "*Recent Circuit Hits & Next-Day Moves:*\n"

        for ev in events[:6]:
            direction_icon = "🟢" if ev['direction'] == 'UPPER' else "🔴"
            move_pct = ev.get('next_day_move_pct')
            next_close = ev.get('next_close')
            if move_pct is not None and next_close is not None:
                next_info = f"Next Day Close: ₹{next_close:.2f} ({move_pct:+.2f}%)"
            else:
                next_info = "Next Day: Pending ⏳"
            time_str = f", {ev['time']} IST" if ev.get('time') else ""
            resp += f"{direction_icon} *{ev['name']}* ({ev['date']}{time_str})\n"
            resp += f"   Extreme: ₹{ev['extreme_hit']} | Day Close: ₹{ev['close_price']}\n"
            resp += f"   {next_info}\n"
            resp += f"   Outcome: {ev.get('next_day_badge', ev.get('rating', ''))}\n\n"

        bot.send_message(message.chat.id, resp, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"An error occurred: {e}")

if __name__ == '__main__':
    bot.infinity_polling()
