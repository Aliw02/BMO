import time
import datetime
import tkinter as tk
from tkinter import messagebox
import winsound

TARGET_HOUR = 9
TARGET_MINUTE = 0

def show_alert():
    root = tk.Tk()
    root.withdraw()
    winsound.Beep(1000, 1000)
    winsound.Beep(1200, 500)
    winsound.Beep(1500, 500)

    msg = (
        "🔔 صارت الساعة 9 الصبح - وقت الشغل! 🔔\n\n"
        "المطلوب منك الآن:\n"
        "1. افتح Telegram\n"
        "2. راجع محادثة NOVA\n"
        "3. اطلب منها: شوف أسئلة التربية الإسلامية 2026 المنشورة\n"
        "4. قارنها مع ملف tawqo3_islamiya_2026_dor1.txt\n"
        "5. رجعلي النتيجة"
    )

    messagebox.showinfo("⏰ 9:00 AM - حان وقت الامتحانات!", msg)
    root.destroy()

def wait_until_9am():
    while True:
        now = datetime.datetime.now()
        if now.hour == TARGET_HOUR and now.minute >= TARGET_MINUTE:
            return
        next_check = now.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE, second=0, microsecond=0)
        if now >= next_check:
            next_check += datetime.timedelta(days=1)
        sleep_seconds = (next_check - now).total_seconds()
        if sleep_seconds > 60:
            sleep_seconds = 30
        time.sleep(sleep_seconds)

if __name__ == "__main__":
    print("⏳ في انتظار الساعة 9:00 صباحاً...")
    wait_until_9am()
    show_alert()
    print("✅ تم التنبيه. روح شوف الـTelegram واطلب من NOVA تبدأ شغلها!")
