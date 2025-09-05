import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import traceback
import hmac
import hashlib
import json
import os
import threading
import gradio as gr
from dotenv import load_dotenv

# ========== CẤU HÌNH ==========
VIETNAM_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
CHART_TYPE = "5m"
LEVERAGE = 30
SL_BUFFER_PERCENT = 0.001
SMALL_WICK_THRESHOLD = 0.05 # Râu nến còn lại phải nhỏ hơn 0.05%

# Lấy cấu hình API OKX từ biến môi trường
load_dotenv()  # Load biến môi trường từ file .env
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_SECRET_KEY = os.environ.get("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE")
OKX_BASE_URL = "https://www.okx.com"

# Cấu hình Slack
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#trading-alerts")

# Kiểm tra nếu các biến môi trường được thiết lập
if not OKX_API_KEY:
    print("❌ Lỗi: Biến môi trường OKX_API_KEY chưa được thiết lập")
    exit(1)
if not OKX_SECRET_KEY:
    print("❌ Lỗi: Biến môi trường OKX_SECRET_KEY chưa được thiết lập")
    exit(1)
if not OKX_PASSPHRASE:
    print("❌ Lỗi: Biến môi trường OKX_PASSPHRASE chưa được thiết lập")
    exit(1)

# <<< TRUNG TÂM CẤU HÌNH CHO TỪNG SYMBOL >>>
SYMBOLS = [
    {
        "symbol": "BTC-USDT-SWAP",
        "wick_threshold": 0.35,
        "position_size_usdt": 10,
        "volume_multiplier": 2.0,
        "rr_ratio": 1.5 # Tỷ lệ R:R
    },
    {
        "symbol": "ETH-USDT-SWAP",
        "wick_threshold": 0.6,
        "position_size_usdt": 10,
        "volume_multiplier": 1.3,
        "rr_ratio": 1.5
    },
    {
        "symbol": "BNB-USDT-SWAP",
        "wick_threshold": 0.4,
        "position_size_usdt": 10,
        "volume_multiplier": 1.5,
        "rr_ratio": 1
    },
    {
        "symbol": "ADA-USDT-SWAP",
        "wick_threshold": 0.8,
        "position_size_usdt": 10,
        "volume_multiplier": 1.3,
        "rr_ratio": 1
    },
    {
        "symbol": "DOGE-USDT-SWAP",
        "wick_threshold": 0.8,
        "position_size_usdt": 10,
        "volume_multiplier": 1.5,
        "rr_ratio": 1
    },
    {
        "symbol": "ETC-USDT-SWAP",
        "wick_threshold": 0.72,
        "position_size_usdt": 10,
        "volume_multiplier": 1.5,
        "rr_ratio": 2
    },
    {
        "symbol": "TON-USDT-SWAP",
        "wick_threshold": 0.55,
        "position_size_usdt": 10,
        "volume_multiplier": 1.5,
        "rr_ratio": 1.5
    }
]

# ======= SLACK FUNCTIONS =======
def send_slack_alert(message, is_critical=False):
    """Gửi cảnh báo đến Slack channel"""
    if not SLACK_WEBHOOK_URL:
        print("⚠️ Slack webhook chưa được cấu hình")
        return
    
    try:
        prefix = "🚨 *CẢNH BÁO NGHIÊM TRỌNG* 🚨\n" if is_critical else "⚠️ *CẢNH BÁO* ⚠️\n"
        formatted_message = prefix + message
        
        payload = {
            "text": formatted_message,
            "channel": SLACK_CHANNEL,
            "username": "Trading Bot",
            "icon_emoji": ":robot_face:"
        }
        
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Đã gửi cảnh báo đến Slack")
        else:
            print(f"❌ Lỗi gửi Slack: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"⚠️ Slack alert error: {e}")

def send_slack_notification(sym_config, candle, analysis, order_info=None):
    """Gửi thông báo nến và lệnh đến Slack"""
    if not SLACK_WEBHOOK_URL:
        return
        
    if analysis["candle_type"] == "other": 
        return
    
    try:
        coin_name = sym_config['symbol'].replace("-SWAP", "")
        timestamp = candle['close_time'].astimezone(VIETNAM_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
        
        # Tạo message cho Slack
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{coin_name} - Nến {analysis['candle_type'].upper()}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*⏰ Thời gian:*\n{timestamp}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*📊 Volume:*\n{analysis['volume']:.2f} (x{analysis['volume_multiplier']:.1f})"
                    }
                ]
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*📈 Open:* {analysis['open']:.8f}\n*📉 Close:* {analysis['close']:.8f}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*🔺 High:* {analysis['high']:.8f}\n*🔻 Low:* {analysis['low']:.8f}"
                    }
                ]
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*🔼 Râu trên:* {analysis['upper_wick_percent']:.4f}%"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*🔽 Râu dưới:* {analysis['lower_wick_percent']:.4f}%"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🎯 Tín hiệu:* {analysis['trend_direction']}"
                }
            }
        ]
        
        # Thêm thông tin lệnh nếu có
        if order_info:
            blocks.extend([
                {
                    "type": "divider"
                },
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "💼 THÔNG TIN LỆNH",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*🎯 Entry:* {order_info['entry_price']:.8f}\n*🛑 Stop Loss:* {order_info['stop_loss']:.8f}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*✅ Take Profit:* {order_info['take_profit']:.8f}\n*📈 R:R:* 1:{sym_config['rr_ratio']}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*⚡ Đòn bẩy:* x{LEVERAGE}\n*💰 Kích thước:* {sym_config['position_size_usdt']} USDT"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*📊 Số lượng:* {order_info['size']} {coin_name.split('-')[0]}"
                        }
                    ]
                }
            ])
        
        payload = {
            "channel": SLACK_CHANNEL,
            "username": "Trading Bot",
            "icon_emoji": ":chart_with_upwards_trend:",
            "blocks": blocks
        }
        
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            print(f"   ✅ Đã gửi thông báo Slack cho {sym_config['symbol']}")
        else:
            print(f"❌ Lỗi gửi Slack: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Lỗi gửi Slack notification: {e}")

def send_slack_balance_alert(sym_config, balance):
    """Gửi cảnh báo số dư đến Slack"""
    if not SLACK_WEBHOOK_URL:
        return
        
    try:
        coin_name = sym_config['symbol'].replace("-SWAP", "")
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "💰 CẢNH BÁO SỐ DƯ",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*📊 Symbol:*\n{coin_name}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*💵 Số dư hiện tại:*\n{balance:.2f} USDT"
                    }
                ]
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*💰 Yêu cầu tối thiểu:*\n{sym_config['position_size_usdt']} USDT"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*❌ Status:*\nKhông đủ số dư"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "⚠️ *Vui lòng nạp thêm tiền vào tài khoản!*"
                }
            }
        ]
        
        payload = {
            "channel": SLACK_CHANNEL,
            "username": "Trading Bot",
            "icon_emoji": ":money_with_wings:",
            "blocks": blocks
        }
        
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            print(f"   ✅ Đã gửi cảnh báo số dư Slack cho {sym_config['symbol']}")
        else:
            print(f"❌ Lỗi gửi Slack balance alert: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Lỗi gửi cảnh báo số dư Slack: {e}")

# ======= OKX API FUNCTIONS =======
def okx_signature(timestamp, method, request_path, body=""):
    message = timestamp + method + request_path + body
    mac = hmac.new(bytes(OKX_SECRET_KEY, 'utf-8'), bytes(message, 'utf-8'), hashlib.sha256)
    return mac.digest().hex()

def okx_request(method, endpoint, params=None, body=None):
    timestamp = datetime.utcnow().isoformat("T", "milliseconds") + "Z"
    request_path = endpoint
    if method == "GET" and params:
        request_path += "?" + "&".join([f"{k}={v}" for k, v in params.items()])
    
    body_str = json.dumps(body) if body else ""
    sign = okx_signature(timestamp, method, request_path, body_str)
    
    headers = {
        'OK-ACCESS-KEY': OKX_API_KEY,
        'OK-ACCESS-SIGN': sign,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': OKX_PASSPHRASE,
        'Content-Type': 'application/json'
    }

    url = OKX_BASE_URL + request_path
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        else: # POST
            response = requests.post(url, headers=headers, data=body_str, timeout=10)
        return response.json()
    except Exception as e:
        print(f"❌ Lỗi OKX API Request: {e}")
        return None

def set_leverage(symbol, leverage):
    endpoint = "/api/v5/account/set-leverage"
    body = {"instId": symbol, "lever": str(leverage), "mgnMode": "isolated"}
    return okx_request("POST", endpoint, body=body)

def place_order(symbol, side, price, sl_price, tp_price, size):
    leverage_result = set_leverage(symbol, LEVERAGE)
    if not leverage_result or leverage_result.get('code') != '0':
        print(f"❌ Lỗi thiết lập đòn bẩy: {leverage_result}")
        return None
        
    endpoint = "/api/v5/trade/order"
    body = {
        "instId": symbol, "tdMode": "isolated", "side": side,
        "ordType": "limit", "px": str(price), "sz": str(size),
        "slTriggerPx": str(sl_price), "slOrdPx": "-1", # Lệnh SL Market
        "tpTriggerPx": str(tp_price), "tpOrdPx": "-1"  # Lệnh TP Market
    }
    return okx_request("POST", endpoint, body=body)

def get_account_balance():
    endpoint = "/api/v5/account/balance"
    params = {"ccy": "USDT"}
    result = okx_request("GET", endpoint, params=params)
    if result and result.get('code') == '0' and result['data']:
        for detail in result['data'][0]['details']:
            if detail['ccy'] == 'USDT':
                return float(detail['availBal'])
    return 0
    
def calculate_position_size(position_size_usdt, entry_price):
    return round(position_size_usdt / entry_price, 6)

# ======= LẤY DỮ LIỆU NẾN TỪ OKX =======
def fetch_last_two_candles(symbol):
    try:
        url = "https://www.okx.com/api/v5/market/history-candles"
        params = {"instId": symbol, "bar": CHART_TYPE, "limit": "2"}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data['code'] != '0' or not data.get('data') or len(data['data']) < 2:
            print(f"❌ Không đủ dữ liệu nến cho {symbol}: {data}")
            return None, None
        def parse_candle(candle_data):
            return {
                "open_time": datetime.fromtimestamp(int(candle_data[0]) / 1000, tz=ZoneInfo("UTC")),
                "open": float(candle_data[1]), "high": float(candle_data[2]),
                "low": float(candle_data[3]), "close": float(candle_data[4]),
                "volume": float(candle_data[5]),
                "close_time": datetime.fromtimestamp(int(candle_data[0]) / 1000 + (5*60-1), tz=ZoneInfo("UTC")),
                "symbol": symbol
            }
        previous_candle = parse_candle(data['data'][1])
        current_candle = parse_candle(data['data'][0])
        return previous_candle, current_candle
    except Exception as e:
        print(f"Lỗi lấy nến {symbol}: {e}")
        return None, None

# ======= KIỂM TRA ĐIỀU KIỆN VOLUME =======
def check_volume_condition(previous_candle, current_candle, sym_config):
    if not previous_candle or not current_candle: return False, 0
    current_volume, previous_volume = current_candle['volume'], previous_candle['volume']
    if previous_volume == 0: return current_volume > 0, 999
    
    volume_multiplier = current_volume / previous_volume
    volume_threshold = sym_config['volume_multiplier']
    
    # ĐÃ XÓA DÒNG IN LOG VỀ VOLUME CHECK
    if volume_multiplier >= volume_threshold:
        return True, volume_multiplier
    else:
        return False, volume_multiplier

# ======= PHÂN TÍCH NẾN =======
def analyze_candle(candle, volume_multiplier, sym_config):
    try:
        open_price, high_price, low_price, close_price = candle["open"], candle["high"], candle["low"], candle["close"]
        wick_threshold = sym_config['wick_threshold']
        
        print(f"\n📊 Phân tích nến {sym_config['symbol']}:")
        
        upper_wick = high_price - max(open_price, close_price)
        upper_wick_percent = (upper_wick / max(open_price, close_price)) * 100 if max(open_price, close_price) > 0 else 0
        
        lower_wick = min(open_price, close_price) - low_price
        lower_wick_percent = (lower_wick / low_price) * 100 if low_price > 0 else 0
        
        print(f"   Râu trên: {upper_wick_percent:.4f}%, Râu dưới: {lower_wick_percent:.4f}%")
        
        candle_type, trend_direction = "other", "Sideways"
        if (close_price < open_price and upper_wick_percent > wick_threshold and lower_wick_percent < SMALL_WICK_THRESHOLD):
            candle_type, trend_direction = "Râu nến trên", "Short"
            print(f"   ⚡ PHÁT HIỆN: Râu nến trên - Tín hiệu Short (Ngưỡng: {wick_threshold}%)")
        elif (close_price > open_price and lower_wick_percent > wick_threshold and upper_wick_percent < SMALL_WICK_THRESHOLD):
            candle_type, trend_direction = "Râu nến dưới", "Long"
            print(f"   ⚡ PHÁT HIỆN: Râu nến dưới - Tín hiệu Long (Ngưỡng: {wick_threshold}%)")
        else:
            print(f"   ➖ Không có tín hiệu râu nến (Ngưỡng: {wick_threshold}%)")

        return {
            "candle_type": candle_type, "open": open_price, "high": high_price,
            "low": low_price, "close": close_price, "volume": candle['volume'],
            "upper_wick_percent": round(upper_wick_percent, 4),
            "lower_wick_percent": round(lower_wick_percent, 4), 
            "trend_direction": trend_direction, "volume_multiplier": round(volume_multiplier, 2)
        }
    except Exception as e:
        print(f"Lỗi phân tích nến {sym_config['symbol']}: {e}")
        return None

# ======= THỰC HIỆN LỆNH GIAO DỊCH =======
def execute_trade(sym_config, analysis, candle):
    try:
        entry_price = analysis['close']
        balance = get_account_balance()
        position_size_usdt = sym_config['position_size_usdt']
        rr_ratio = sym_config['rr_ratio']

        if balance < position_size_usdt:
            print(f"❌ Số dư không đủ: {balance:.2f} USDT (cần {position_size_usdt} USDT)")
            send_slack_balance_alert(sym_config, balance)
            return None
        
        position_size = calculate_position_size(position_size_usdt, entry_price)
        
        side, sl_price, tp_price = None, None, None
        if analysis['candle_type'] == "Râu nến trên":
            side = "sell"
            sl_price = analysis['high']
            risk = sl_price - entry_price
            tp_price = entry_price - (risk * rr_ratio)
        elif analysis['candle_type'] == "Râu nến dưới":
            side = "buy"
            sl_price = analysis['low']
            risk = entry_price - sl_price
            tp_price = entry_price + (risk * rr_ratio)
        
        if not side: return None

        print(f"🎯 Đặt lệnh {side.upper()} {sym_config['symbol']}")
        print(f"   Kích thước: {position_size_usdt} USDT, Số lượng: {position_size}")
        print(f"   Entry: {entry_price:.8f}, SL: {sl_price:.8f}, TP: {tp_price:.8f}, R:R: 1:{rr_ratio}")

        order_result = place_order(sym_config['symbol'], side, entry_price, sl_price, tp_price, position_size)
        
        if order_result and order_result.get('code') == '0':
            print(f"✅ Lệnh {side} thành công cho {sym_config['symbol']}")
            return {'entry_price': entry_price, 'stop_loss': sl_price, 'take_profit': tp_price, 'size': position_size, 'side': side}
        else:
            print(f"❌ Lỗi đặt lệnh: {order_result}")
            return None
    except Exception as e:
        print(f"❌ Lỗi thực hiện lệnh: {e}")
        return None

# ======= TRADING BOT TASK =======
last_checked_candle_time = None

def trading_bot_task():
    """Hàm thực hiện công việc trading"""
    global last_checked_candle_time
    print("🔍 Đang kiểm tra dữ liệu...")
    
    try:
        now_utc = datetime.now(ZoneInfo("UTC"))
        current_candle_start_time = now_utc.replace(second=0, microsecond=0, minute=now_utc.minute // 5 * 5)
        
        if current_candle_start_time != last_checked_candle_time:
            last_checked_candle_time = current_candle_start_time
            print(f"\n{'='*50}\n🕒 Đang kiểm tra nến lúc: {now_utc.astimezone(VIETNAM_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}\n{'='*50}")

            for sym_config in SYMBOLS:
                previous_candle, current_candle = fetch_last_two_candles(sym_config['symbol'])
                if not current_candle: 
                    continue

                volume_condition, volume_multiplier = check_volume_condition(previous_candle, current_candle, sym_config)
                if not volume_condition:
                    print(f"➖ {sym_config['symbol']} | 📊 Volume không đạt ngưỡng.")
                    continue
                
                analysis = analyze_candle(current_candle, volume_multiplier, sym_config)
                if analysis and analysis["candle_type"] != "other":
                    print(f"✔️ {sym_config['symbol']} | {analysis['candle_type']}")
                    order_info = execute_trade(sym_config, analysis, current_candle)
                    send_slack_notification(sym_config, current_candle, analysis, order_info)
                else:
                    print(f"➖ {sym_config['symbol']} | Volume đạt nhưng không có tín hiệu nến.")
            print(f"\n⏳ Kiểm tra xong, chờ nến tiếp theo...")
                
    except Exception as e:
        error_msg = f"LỖI TRONG TASK:\n{e}\n{traceback.format_exc()}"
        print(error_msg)
        if SLACK_WEBHOOK_URL:
            send_slack_alert(f"```{error_msg}```", is_critical=True)

def run_check():
    """Hàm chạy trading bot task trong thread riêng"""
    thread = threading.Thread(target=trading_bot_task)
    thread.start()
    next_run_time = datetime.now(ZoneInfo("UTC")).replace(second=0, microsecond=0)
    next_run_time = next_run_time.replace(minute=((next_run_time.minute // 5) + 1) * 5)
    if next_run_time.minute >= 60:
        next_run_time = next_run_time.replace(hour=next_run_time.hour + 1, minute=0)
    
    return f"🟢 Đã kích hoạt kiểm tra trong nền.\n⏰ Lần chạy tiếp theo: {next_run_time.astimezone(VIETNAM_TIMEZONE).strftime('%H:%M:%S')}"

# ======= SCHEDULED TASK =======
def scheduled_task():
    """Chạy tự động vào các khung 5 phút"""
    while True:
        now = datetime.now(ZoneInfo("UTC"))
        # Chạy vào giây thứ 5 của mỗi phút thứ 0, 5, 10, ..., 55
        if now.minute % 5 == 0 and now.second == 5:
            print(f"\n⏰ Đến giờ chạy scheduled task: {now.astimezone(VIETNAM_TIMEZONE).strftime('%H:%M:%S')}")
            trading_bot_task()
            # Chờ 55 giây để tránh chạy nhiều lần trong cùng 1 phút
            time.sleep(55)
        else:
            time.sleep(0.5)

# ======= MAIN FUNCTION =======
def main():
    print("🟢 Bot đang chạy...")
    print(f"📊 Sử dụng API Key: {OKX_API_KEY[:8]}...{OKX_API_KEY[-4:]}")
    
    if SLACK_WEBHOOK_URL:
        send_slack_alert("Bot giao dịch OKX đã khởi động", is_critical=False)
    
    # Khởi chạy scheduled task trong thread riêng
    scheduler_thread = threading.Thread(target=scheduled_task, daemon=True)
    scheduler_thread.start()
    print("✅ Scheduled task đã được khởi chạy")
    
    # Tạo giao diện Gradio
    with gr.Blocks(title="Trading Bot") as demo:
        gr.Markdown("# 🤖 Trading Bot - Phát hiện nến râu & giao dịch tự động")
        gr.Markdown("Bot sẽ tự động chạy vào các khung 5 phút (00:00, 00:05, 00:10, ...)")
        
        status_output = gr.Textbox(
            label="Trạng thái Bot", 
            lines=10, 
            interactive=False,
            value="🟢 Bot đang chạy...\n📊 Tự động chạy vào các khung 5 phút\n\nNhấn nút 'Chạy Kiểm Tra Ngay' để kiểm tra thủ công."
        )
        run_button = gr.Button("🔄 Chạy Kiểm Tra Ngay")
        
        run_button.click(fn=run_check, outputs=status_output)
    
    # Khởi chạy ứng dụng Gradio
    demo.launch(share=False)

if __name__ == "__main__":
    main()
    