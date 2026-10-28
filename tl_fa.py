"""
ابزارهای مشترک فارسی برای همه‌ی گزارش‌های TradingLife.

- POST_LANG: زبان پست‌ها ("fa" پیش‌فرض، "en" برای کانال انگلیسی آینده)
- فونت Vazirmatn + موتور raqm خود Pillow (روش تأییدشده؛ arabic_reshaper اتصال‌ها رو خراب می‌کنه)
- رسم و شکستن خط متن راست‌به‌چپ
- ساعت تهران
"""

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

POST_LANG = os.environ.get("POST_LANG", "fa").strip().lower()
IS_FA = POST_LANG == "fa"

TEHRAN = ZoneInfo("Asia/Tehran")
NEW_YORK = ZoneInfo("America/New_York")
DISPLAY_TZ = TEHRAN if IS_FA else NEW_YORK
TZ_LABEL = "به وقت تهران" if IS_FA else "ET"

FONT_URL = "https://raw.githubusercontent.com/rastikerdar/vazirmatn/master/fonts/ttf/Vazirmatn-{w}.ttf"
_FONT_PATHS = {}

FA_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
FA_MONTHS = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر",
             "اکتبر", "نوامبر", "دسامبر"]


# ---------------------------------------------------------------- تاریخ و ساعت

def local(dt):
    """زمان رو به منطقه‌ی نمایش (تهران برای فارسی) تبدیل می‌کنه."""
    return dt.astimezone(DISPLAY_TZ)


def fa_date(d, weekday=True, year=True):
    """مثلاً «چهارشنبه ۲۸ اکتبر 2026» (روز و سال با ارقام لاتین)."""
    s = f"{d.day} {FA_MONTHS[d.month - 1]}"
    if year:
        s += f" {d.year}"
    if weekday:
        s = f"{FA_WEEKDAYS[d.weekday()]} {s}"
    return s


def fa_time(dt):
    return local(dt).strftime("%H:%M")


# ---------------------------------------------------------------- فونت و رسم راست‌به‌چپ

def _font_path(weight):
    if weight in _FONT_PATHS:
        return _FONT_PATHS[weight]
    path = f"/tmp/Vazirmatn-{weight}.ttf"
    if not os.path.exists(path) or os.path.getsize(path) < 50000:
        import requests
        r = requests.get(FONT_URL.format(w=weight), timeout=30)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
    _FONT_PATHS[weight] = path
    return path


def fa_font(size, bold=False):
    """فونت Vazirmatn با موتور raqm. اگه raqm نباشه، خطا می‌ده (متن فارسی بدون raqm غلط رسم می‌شه)."""
    from PIL import ImageFont, features
    if not features.check("raqm"):
        raise RuntimeError("Pillow raqm layout engine is not available; Persian text would render incorrectly.")
    return ImageFont.truetype(_font_path("Bold" if bold else "Regular"), size,
                              layout_engine=ImageFont.Layout.RAQM)


def has_rtl(text):
    return bool(re.search(r"[؀-ۿ]", text or ""))


def bidi_fix(text):
    """
    در متن راست‌به‌چپ، «0.3%» یا «+1.2%» یا «$82» گاهی به‌هم می‌ریزه (مثلاً %0.3).
    هر عدد لاتین با علامت‌هاش رو در یک «جاسازی چپ‌به‌راست» (LRE…PDF) قرار می‌دیم.
    (ایزوله‌های LRI/PDI در موتور raqm خود Pillow کل خط رو به‌هم می‌ریزن؛ LRE تست شده و درسته.)
    """
    if not has_rtl(text):
        return text
    return re.sub(r"[+\-\u2212]?\$?\d[\d,]*(?:\.\d+)?[%KMBT]?",
                  lambda m: "\u202a" + m.group(0) + "\u202c", text)


def text_w(draw, text, font):
    if has_rtl(text):
        return draw.textlength(bidi_fix(text), font=font, direction="rtl")
    return draw.textlength(text, font=font)


def draw_r(draw, x_right, y, text, font, fill):
    """متن رو طوری می‌کشه که لبه‌ی راستش روی x_right باشه (فارسی راست‌به‌چپ)."""
    if has_rtl(text):
        draw.text((x_right, y), bidi_fix(text), font=font, fill=fill, direction="rtl", anchor="ra")
    else:
        draw.text((x_right - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def draw_l(draw, x_left, y, text, font, fill):
    """متن رو طوری می‌کشه که لبه‌ی چپش روی x_left باشه."""
    if has_rtl(text):
        draw.text((x_left + text_w(draw, text, font), y), bidi_fix(text), font=font, fill=fill,
                  direction="rtl", anchor="ra")
    else:
        draw.text((x_left, y), text, font=font, fill=fill)


def wrap(draw, text, font, width):
    lines, cur = [], ""
    for w in (text or "").split():
        t = (cur + " " + w).strip()
        if text_w(draw, t, font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- عدد

FA_TO_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬", "01234567890123456789.,")


def latin_digits(s):
    return (s or "").translate(FA_TO_LATIN)


def numbers_in(text):
    """عددهای یک متن (فارسی یا لاتین) به شکل نرمال‌شده — برای چک عدد ساختگی در خلاصه‌ها."""
    t = latin_digits(text)
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", t)}


def hashtags(*tags):
    """هشتگ‌ها رو یکتا و تمیز برمی‌گردونه (فاصله → زیرخط)."""
    out = []
    for t in tags:
        if not t:
            continue
        t = "#" + re.sub(r"\s+", "_", t.strip().lstrip("#"))
        if t not in out:
            out.append(t)
    return " ".join(out)


# ---------------------------------------------------------------- اسم فارسی رویدادهای تقویم اقتصادی
# (الگوی عنوان Forex Factory → اسم فارسی). رویدادی که اینجا نیست، با اسم انگلیسی نمایش داده می‌شه.
_SUFFIX = [(r" m/m$", " (ماهانه)"), (r" q/q$", " (فصلی)"), (r" y/y$", " (سالانه)")]
EVENT_FA = [
    (r"^ADP Non-Farm Employment Change", "اشتغال بخش خصوصی ADP"),
    (r"^Non-Farm Employment Change", "اشتغال غیرکشاورزی (NFP)"),
    (r"^Unemployment Rate", "نرخ بیکاری"),
    (r"^Unemployment Claims", "مدعیان بیکاری هفتگی"),
    (r"^Average Hourly Earnings", "متوسط دستمزد ساعتی"),
    (r"^JOLTS Job Openings", "فرصت‌های شغلی JOLTS"),
    (r"^Employment Change", "تغییر اشتغال"),
    (r"^Core CPI", "تورم هسته (CPI هسته)"),
    (r"^CPI", "تورم مصرف‌کننده (CPI)"),
    (r"^Trimmed Mean CPI", "تورم میانگین پیراسته"),
    (r"^Core PCE Price Index", "تورم PCE هسته"),
    (r"^PCE Price Index", "تورم PCE"),
    (r"^Core PPI", "تورم تولیدکننده‌ی هسته (PPI)"),
    (r"^PPI", "تورم تولیدکننده (PPI)"),
    (r"^Advance GDP|^Prelim GDP|^Final GDP|^GDP", "تولید ناخالص داخلی (GDP)"),
    (r"^Core Retail Sales", "خرده‌فروشی هسته"),
    (r"^Retail Sales", "خرده‌فروشی"),
    (r"^ISM Manufacturing PMI", "شاخص مدیران خرید تولیدی ISM"),
    (r"^ISM Services PMI", "شاخص مدیران خرید خدمات ISM"),
    (r"^ISM Manufacturing Prices", "قیمت‌ها در بخش تولیدی ISM"),
    (r"^Flash Manufacturing PMI", "PMI تولیدی (مقدماتی)"),
    (r"^Flash Services PMI", "PMI خدمات (مقدماتی)"),
    (r"^CB Consumer Confidence", "اعتماد مصرف‌کننده (CB)"),
    (r"UoM Consumer Sentiment", "احساسات مصرف‌کننده (میشیگان)"),
    (r"UoM Inflation Expectations", "انتظارات تورمی (میشیگان)"),
    (r"^Federal Funds Rate", "نرخ بهره‌ی فدرال رزرو"),
    (r"^FOMC Statement", "بیانیه‌ی فدرال رزرو"),
    (r"^FOMC Press Conference", "کنفرانس خبری فدرال رزرو"),
    (r"^FOMC Economic Projections", "پیش‌بینی‌های اقتصادی فدرال رزرو"),
    (r"^FOMC Meeting Minutes", "صورت‌جلسه‌ی فدرال رزرو"),
    (r"^Fed Chair .* Speaks|^Fed Chair .* Testifies", "سخنرانی رئیس فدرال رزرو"),
    (r"^FOMC Member .* Speaks", "سخنرانی عضو فدرال رزرو"),
    (r"^President Trump Speaks", "سخنرانی ترامپ"),
    (r"^Durable Goods Orders", "سفارش کالاهای بادوام"),
    (r"^Core Durable Goods Orders", "سفارش کالاهای بادوام هسته"),
    (r"^Building Permits", "مجوزهای ساخت"),
    (r"^Housing Starts", "شروع ساخت مسکن"),
    (r"^New Home Sales", "فروش خانه‌ی نوساز"),
    (r"^Existing Home Sales", "فروش خانه‌های موجود"),
    (r"^Pending Home Sales", "قراردادهای فروش مسکن"),
    (r"^Empire State Manufacturing", "شاخص تولیدی نیویورک"),
    (r"^Philly Fed Manufacturing", "شاخص تولیدی فیلادلفیا"),
    (r"^Richmond Manufacturing", "شاخص تولیدی ریچموند"),
    (r"^Trade Balance", "تراز تجاری"),
    (r"^Crude Oil Inventories", "ذخایر نفت خام آمریکا"),
    (r"^Natural Gas Storage", "ذخایر گاز طبیعی"),
    (r"^Industrial Production", "تولید صنعتی"),
    (r"^Bank Holiday", "تعطیلی رسمی"),
    (r"^Cash Rate", "نرخ بهره"),
    (r"^Official Bank Rate", "نرخ بهره‌ی بانک انگلستان"),
    (r"^Main Refinancing Rate", "نرخ بهره‌ی بانک مرکزی اروپا"),
    (r"^Monetary Policy Statement|^RBA Rate Statement|^Rate Statement", "بیانیه‌ی سیاست پولی"),
    (r"^ECB Press Conference", "کنفرانس خبری بانک مرکزی اروپا"),
    (r"^BOJ Policy Rate", "نرخ بهره‌ی بانک ژاپن"),
    (r"^BOJ Press Conference", "کنفرانس خبری بانک ژاپن"),
    (r"^SNB Policy Rate", "نرخ بهره‌ی بانک ملی سوئیس"),
    (r"^SNB Press Conference", "کنفرانس خبری بانک ملی سوئیس"),
    (r"^Overnight Rate", "نرخ بهره‌ی بانک کانادا"),
    (r"^Official Cash Rate", "نرخ بهره‌ی بانک نیوزیلند"),
    (r"^BOE Gov .* Speaks", "سخنرانی رئیس بانک انگلستان"),
    (r"^ECB President .* Speaks", "سخنرانی رئیس بانک مرکزی اروپا"),
    (r"^RBA Gov .* Speaks", "سخنرانی رئیس بانک مرکزی استرالیا"),
    (r"^BOJ Gov .* Speaks", "سخنرانی رئیس بانک ژاپن"),
    (r"^German ", "آلمان: "),
    (r"^French ", "فرانسه: "),
    (r"^Daylight Saving Time Shift", "تغییر ساعت رسمی"),
]


def event_fa(title):
    t = (title or "").strip()
    for pat, name in EVENT_FA:
        if re.search(pat, t, re.I):
            if name.endswith(": "):                      # پیشوند کشور + بقیه‌ی عنوان
                return name + event_fa(re.sub(pat, "", t, flags=re.I))
            for sp, sname in _SUFFIX:
                if re.search(sp, t) and sname not in name:
                    return name + sname
            return name
    return t


# ---------------------------------------------------------------- کارت آینه‌ای (راست‌به‌چپ) برای کارت‌های موجود
# به‌جای بازنویسی کامل هر کارت، رسم‌ها از این لایه رد می‌شن:
#   - همه‌ی مختصات افقی آینه می‌شن (چپ ↔ راست)
#   - هر متن از «مترجم» رد می‌شه؛ اگه فارسی شد با فونت Vazirmatn و راست‌به‌چپ رسم می‌شه
#   - نمودارها (خط‌هایی با بیش از ۲ نقطه) جابه‌جا می‌شن ولی جهت زمان (چپ → راست) حفظ می‌شه

class Translator:
    """قوانین (الگو، جایگزین) به ترتیب امتحان می‌شن؛ اولین تطابق کامل اعمال می‌شه."""

    def __init__(self, rules):
        self.rules = [(re.compile(p), r) for p, r in rules]

    def __call__(self, text):
        if not text or has_rtl(text):
            return text
        for rx, rep in self.rules:
            m = rx.fullmatch(text)
            if m:
                return rep(m) if callable(rep) else m.expand(rep)
        return text


class MirrorDraw:
    def __init__(self, draw, width, translate=None, fa_scale=0.96):
        self.d = draw
        self.W = width
        self.tr = translate or (lambda t: t)
        self.scale = fa_scale
        self._fonts = {}

    # ---- فونت
    def _fa(self, font):
        size = getattr(font, "size", 20)
        bold = "bold" in " ".join(font.getname()).lower() if hasattr(font, "getname") else False
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = fa_font(max(10, round(size * self.scale)), bold)
        return self._fonts[key]

    def _prep(self, text, font):
        t = self.tr(text)
        return (t, self._fa(font)) if has_rtl(t) else (t, font)

    # ---- متن
    def textlength(self, text, font=None, **kw):
        t, f = self._prep(text, font)
        return text_w(self.d, t, f)

    def textbbox(self, xy, text, font=None, **kw):
        w = self.textlength(text, font)
        b = self.d.textbbox(xy, "Hg", font=font)
        return (xy[0], b[1], xy[0] + w, b[3])

    def text(self, xy, text, font=None, fill=None, anchor=None, **kw):
        x, y = xy
        t, f = self._prep(str(text), font)
        if has_rtl(t):
            y -= round(getattr(font, "size", 20) * 0.12)       # تراز عمودی Vazirmatn با DejaVu
        if anchor and anchor[0] == "r":                         # متن راست‌چین اصلی → چپ‌چین در آینه
            draw_l(self.d, self.W - x, y, t, f, fill)
        elif anchor and anchor[0] == "m":
            w = text_w(self.d, t, f)
            draw_l(self.d, self.W - x - w / 2, y, t, f, fill)
        else:                                                   # متن چپ‌چین اصلی → راست‌چین در آینه
            draw_r(self.d, self.W - x, y, t, f, fill)

    # ---- شکل‌ها
    def _box(self, xy):
        x1, y1, x2, y2 = xy
        return (self.W - x2, y1, self.W - x1, y2)

    def rounded_rectangle(self, xy, *a, **kw):
        return self.d.rounded_rectangle(self._box(xy), *a, **kw)

    def rectangle(self, xy, *a, **kw):
        return self.d.rectangle(self._box(xy), *a, **kw)

    def ellipse(self, xy, *a, **kw):
        return self.d.ellipse(self._box(xy), *a, **kw)

    def line(self, xy, *a, **kw):
        pts = list(xy)
        if pts and not isinstance(pts[0], (tuple, list)):
            pts = [(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)]
        if len(pts) > 2:                                        # نمودار: جهت زمان حفظ می‌شه
            lo, hi = min(p[0] for p in pts), max(p[0] for p in pts)
            shift = (self.W - hi) - lo
            pts = [(p[0] + shift, p[1]) for p in pts]
        else:
            pts = [(self.W - p[0], p[1]) for p in pts]
        return self.d.line(pts, *a, **kw)

    def __getattr__(self, name):
        return getattr(self.d, name)


# ---------------------------------------------------------------- ترجمه‌ی فارسی خلاصه‌ی خبرها (با چک)
FA_NEWS_RULES = (
    "- ALSO give a faithful Persian (Farsi) translation of your English sentence as summary_fa, in clear, "
    "natural, neutral Persian for a general audience. Keep every number exactly as in the English sentence, "
    "written with Latin digits (e.g. 4.3%, $82). Keep names of institutions/companies/tokens in their common "
    "Persian form or in English (e.g. فدرال رزرو، SEC، Bitcoin، Goldman Sachs). Keep attributions "
    "(e.g. 'به گزارش …'). Add nothing that is not in the English sentence.\n"
    "- label_fa = 1-2 word Persian topic.\n"
)
FA_NEWS_FIELDS = '"label_fa":"<موضوع>","summary_fa":"<ترجمه>"'
ATTRIB_FA = r"به گزارش|به گفته|گزارش داد|گزارش کرد|گفت|اعلام کرد|طبق|به نقل از|می‌گوید|گفته"


def fa_check(sel, summary_en, source_text, default_label="بازار"):
    """
    ترجمه‌ی فارسی یک خلاصه‌ی انگلیسیِ تأییدشده رو چک می‌کنه. خروجی: (متن، برچسب، دلیل رد)
    """
    fa = re.sub(r"[【〖][^】〗]*[】〗]", "", str(sel.get("summary_fa", ""))).strip()
    lab = str(sel.get("label_fa", "")).strip().rstrip(":")
    if not has_rtl(fa) or not 20 <= len(fa) <= 320:
        return None, None, "ترجمه‌ی فارسی نامعتبر"
    extra = numbers_in(fa) - numbers_in(source_text)
    if extra:
        return None, None, f"عدد ساختگی در فارسی {extra}"
    if re.search(r"\b(report|reported|reports|according|citing|said|says|told)\b", summary_en, re.I) \
            and not re.search(ATTRIB_FA, fa):
        return None, None, "منبعِ گزارش در ترجمه‌ی فارسی حذف شده"
    if not has_rtl(lab) or len(lab) > 20:
        lab = default_label
    return fa, lab, None
