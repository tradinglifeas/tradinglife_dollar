"""
نکته‌ی روزانه‌ی «روانشناسی معامله‌گری» برای کانال (فارسی؛ با POST_LANG=en انگلیسی).

موضوع آموزشی و همیشگیه، پس به خبر وابسته نیست. هر روز یکی از ۳۰ موضوع (بر اساس روز سال)
انتخاب می‌شه و Groq یک نکته‌ی کوتاه درباره‌ش می‌نویسه.

ایمنی (اصلاح‌شده نسبت به نسخه‌ی قبلی):
  - خروجی JSON ساختاریافته (عنوان + متن)؛ «فکرهای داخلی» مدل هیچ‌وقت منتشر نمی‌شه
  - متن نباید عدد، درصد، آمار یا توصیه‌ی خرید/فروش داشته باشه (جلوی آمار ساختگی)
  - طول و زبان متن چک می‌شه؛ اگه معتبر نبود، با مدل دیگه دوباره تلاش می‌شه
  - اگه هیچ متن معتبری ساخته نشد، پستی نمی‌ره و اجرا قرمز می‌شه
"""

import os
import re
import json
import time
import html
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from tl_fa import IS_FA, has_rtl, hashtags

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

# (موضوع انگلیسی برای مدل، اسم فارسی برای هشتگ)
TOPICS = [
    ("Loss aversion", "زیان_گریزی"),
    ("FOMO (fear of missing out)", "فومو"),
    ("Revenge trading", "معامله_انتقامی"),
    ("Overconfidence after a winning streak", "اعتماد_به_نفس_کاذب"),
    ("The importance of a trading plan", "برنامه_معاملاتی"),
    ("Confirmation bias", "سوگیری_تأیید"),
    ("Position sizing and risk management mindset", "مدیریت_ریسک"),
    ("Patience and waiting for setups", "صبر"),
    ("Anchoring bias on entry price", "سوگیری_لنگر"),
    ("The sunk cost fallacy in open positions", "هزینه_هدررفته"),
    ("Overtrading and boredom", "معامله_بیش_از_حد"),
    ("Journaling trades for self-awareness", "ژورنال_معاملاتی"),
    ("Managing emotions after a big loss", "مدیریت_احساسات"),
    ("The danger of averaging down without a plan", "میانگین_کم_کردن"),
    ("Recency bias in market outlook", "سوگیری_تازگی"),
    ("Discipline vs. impulsiveness", "انضباط"),
    ("Accepting uncertainty in the markets", "پذیرش_عدم_قطعیت"),
    ("The role of routine in consistent performance", "روتین_معاملاتی"),
    ("Herd mentality and crowd psychology", "رفتار_گله‌ای"),
    ("Dealing with drawdowns mentally", "افت_سرمایه"),
    ("The illusion of control in trading", "توهم_کنترل"),
    ("Setting realistic expectations", "انتظارات_واقع_بینانه"),
    ("Detaching self-worth from P&L", "عزت_نفس"),
    ("The value of taking breaks after losses", "استراحت"),
    ("Cognitive fatigue and decision quality", "خستگی_ذهنی"),
    ("Survivorship bias from social media trading stories", "سوگیری_بقا"),
    ("The role of humility in long-term success", "فروتنی"),
    ("Analysis paralysis", "فلج_تحلیلی"),
    ("Letting winners run vs. fear of giving back gains", "حفظ_سود"),
    ("Building emotional resilience over time", "تاب_آوری"),
]

# در نکته‌ی آموزشی، عدد معمولاً یعنی آمار یا قانون ساختگی؛ توصیه‌ی معامله هم ممنوعه
FORBIDDEN_FA = r"\d|[۰-۹]|٪|درصد|بخرید|بفروشید|خرید کنید|فروش کنید|سیگنال|تضمین"
FORBIDDEN_EN = r"\d|%|percent|\bbuy\b|\bsell\b|guarantee|signal"


def call_groq(payload, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            data = requests.post(GROQ_URL, json=payload, headers=headers, timeout=60).json()
        except (requests.RequestException, ValueError) as e:
            print(f"خطا در Groq: {e}")
            return None
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") == "rate_limit_exceeded":
            m = re.search(r"try again in ([\d.]+)s", err.get("message", ""))
            time.sleep(min(float(m.group(1)) + 2 if m else 15, 30))
            continue
        if err:
            print(f"خطای Groq: {err}")
            return None
        return data
    return None


def build_prompt(topic):
    if IS_FA:
        return (
            f'Write a short trading-psychology tip for a Persian-language financial Telegram channel, '
            f'on the topic: "{topic}".\n'
            "Write it in clear, natural, warm but neutral Persian (Farsi) for a general audience.\n"
            "title_fa: the concept's name in Persian (max 6 words).\n"
            "body_fa: one short paragraph of 3-4 sentences: what it is, why it affects traders, and one "
            "practical, general way to manage it.\n"
            "STRICT RULES: no numbers, percentages or statistics of any kind; no specific trading "
            "instructions, no buy/sell advice, no promises of results; no quotes or sources; plain text.\n"
            'Return ONLY JSON: {"title_fa":"...","body_fa":"..."}'
        )
    return (
        f'Write a short trading-psychology tip for a financial Telegram channel, on the topic: "{topic}".\n'
        "title: the concept's name (max 6 words). body: one paragraph of 3-4 sentences: what it is, why it "
        "affects traders, and one practical, general way to manage it. Calm, educational, neutral.\n"
        "STRICT RULES: no numbers, percentages or statistics; no buy/sell advice; no promises; plain text.\n"
        'Return ONLY JSON: {"title":"...","body":"..."}'
    )


def valid(tip):
    t = re.sub(r"[【〖][^】〗]*[】〗]", "", tip.get("title_fa" if IS_FA else "title", "")).strip()
    b = re.sub(r"[【〖][^】〗]*[】〗]", "", tip.get("body_fa" if IS_FA else "body", "")).strip()
    if IS_FA and not (has_rtl(t) and has_rtl(b)):
        return None, "متن فارسی نیست"
    if not (3 <= len(t) <= 60 and 150 <= len(b) <= 900):
        return None, f"طول نامناسب (عنوان {len(t)}، متن {len(b)})"
    bad = re.search(FORBIDDEN_FA if IS_FA else FORBIDDEN_EN, t + " " + b, re.I)
    if bad:
        return None, f"عبارت ممنوع: {bad.group(0)!r}"
    return (t, b), None


def get_tip(topic):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for model in GROQ_MODELS:
        for effort in ("low", "medium"):
            data = call_groq({"model": model, "messages": [{"role": "user", "content": build_prompt(topic)}],
                              "temperature": 0.6, "max_tokens": 2000, "reasoning_effort": effort}, headers)
            if not data:
                continue
            try:
                raw = (data["choices"][0]["message"].get("content") or "").strip()   # هیچ‌وقت reasoning
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                tip = json.loads(m.group(0)) if m else {}
            except (KeyError, IndexError, TypeError, ValueError) as e:
                print(f"خروجی Groq ({model}/{effort}) قابل خواندن نبود: {e}")
                continue
            res, why = valid(tip)
            if res:
                print(f"قبول شد ({model}/{effort}): {res[0]}")
                return res
            print(f"رد شد ({model}/{effort}): {why}")
    return None


def send_to_telegram(text):
    r = requests.post(TELEGRAM_API_URL, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                                              "disable_web_page_preview": True}, timeout=20)
    res = r.json()
    if not res.get("ok"):
        print(f"خطا در ارسال پیام: {res}")
        raise SystemExit(1)
    print("نکته‌ی روانشناسی ارسال شد.")


def main():
    ny = datetime.now(ZoneInfo("America/New_York"))
    topic, tag_fa = TOPICS[int(ny.strftime("%j")) % len(TOPICS)]
    print("موضوع امروز:", topic)
    tip = get_tip(topic)
    if not tip:
        raise SystemExit("نکته‌ی معتبری ساخته نشد. پستی ارسال نشد.")
    title, body = (html.escape(x) for x in tip)
    if IS_FA:
        lines = ["🧠 <b>روانشناسی معامله‌گری</b>", "", f"<b>{title}</b>", body, "",
                 "<i>محتوای آموزشی؛ توصیه‌ی سرمایه‌گذاری نیست.</i>", "",
                 hashtags("روانشناسی_معامله_گری", tag_fa)]
    else:
        lines = ["🧠 <b>Trading Psychology Tip of the Day</b>", "", f"<b>{title}</b>", body, "",
                 "<i>Educational content; not investment advice.</i>", "", "#TradingPsychology"]
    send_to_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
