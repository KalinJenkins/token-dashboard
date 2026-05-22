#!/usr/bin/env python3
"""
PiTFT API Credit Dashboard — Pillow/framebuffer renderer
Draws directly to /dev/fb0, no SDL/pygame required.

Button mapping (BCM GPIO, active-low):
  GPIO 17  Button 1 (leftmost)  → Refresh now
  GPIO 22  Button 2             → (reserved)
  GPIO 23  Button 3             → (reserved)
  GPIO 27  Button 4 (rightmost) → Toggle backlight
"""

import os
import sys
import time
import struct
import threading
import logging
from datetime import datetime

import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

from fetchers import fetch_all

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL_SECONDS", 300))
W, H      = 320, 240
FB_DEV    = os.getenv("SDL_FBDEV", "/dev/fb0")
FPS       = 5   # renders per second — low is fine, saves CPU

BTN_REFRESH   = 17
BTN_UNUSED_2  = 22
BTN_UNUSED_3  = 23
BTN_BACKLIGHT = 27
BACKLIGHT_PIN = 18

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Palette (R,G,B) ───────────────────────────────────────────────────────────
BG          = (10,  10,  18)
CARD_BG     = (18,  20,  32)
CARD_BORDER = (38,  42,  66)
TITLE_COL   = (155, 160, 210)
VALUE_COL   = (235, 238, 255)
LABEL_COL   = (95,  100, 138)
GOOD        = (72,  200, 110)
WARN        = (225, 175,  45)
ERR         = (215,  65,  65)
BLUE        = (50,  115, 200)
SEP         = (32,  36,  54)
HINT        = (85,   90, 120)

# ── Framebuffer writer ────────────────────────────────────────────────────────

def get_fb_info():
    """Return (bits_per_pixel, is_bgr) by reading /sys."""
    try:
        bpp = int(open("/sys/class/graphics/fb0/bits_per_pixel").read().strip())
    except Exception:
        bpp = 16
    # Most PiTFT ILI9340 setups use RGB565 (not BGR)
    return bpp

def image_to_fb(img: Image.Image, fb_path: str, bpp: int):
    """Write a PIL Image to the framebuffer."""
    if bpp == 32:
        raw = img.convert("RGBA").tobytes()
    else:
        # RGB565
        rgb = img.convert("RGB")
        pixels = list(rgb.getdata())
        buf = bytearray(len(pixels) * 2)
        for i, (r, g, b) in enumerate(pixels):
            val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            struct.pack_into("<H", buf, i * 2, val)
        raw = bytes(buf)
    try:
        with open(fb_path, "wb") as f:
            f.write(raw)
    except Exception as e:
        log.error(f"FB write error: {e}")

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono{}.ttf".format("-Bold" if bold else ""),
        "/usr/share/fonts/truetype/freefont/FreeMono{}.ttf".format("Bold" if bold else ""),
        "/usr/share/fonts/truetype/liberation/LiberationMono-{}.ttf".format("Bold" if bold else "Regular"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def fmt_dollars(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"

def fmt_k(v):
    if not isinstance(v, (int, float)): return "—"
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}K"
    return str(int(v))

def pct_colour(pct):
    if pct >= 0.9: return ERR
    if pct >= 0.7: return WARN
    return GOOD

def val_colour(v, warn=5.0, err=1.0):
    if not isinstance(v, (int, float)): return ERR
    if v <= err:  return ERR
    if v <= warn: return WARN
    return GOOD

# ── GPIO ──────────────────────────────────────────────────────────────────────

def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in (BTN_REFRESH, BTN_UNUSED_2, BTN_UNUSED_3, BTN_BACKLIGHT):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(BACKLIGHT_PIN, GPIO.OUT)
    GPIO.output(BACKLIGHT_PIN, GPIO.HIGH)

def cleanup_gpio():
    GPIO.cleanup()

# ── Renderer ──────────────────────────────────────────────────────────────────

class Renderer:
    def __init__(self):
        self.f_xl  = load_font(20, bold=True)
        self.f_lg  = load_font(15, bold=True)
        self.f_md  = load_font(13)
        self.f_sm  = load_font(11)
        self.f_xs  = load_font(10)

    def _txt(self, draw, text, font, colour, x, y, anchor="la"):
        draw.text((x, y), str(text), font=font, fill=colour, anchor=anchor)

    def _bar(self, draw, x, y, w, h, pct, colour):
        pct = max(0.0, min(1.0, pct))
        draw.rectangle([x, y, x+w, y+h], fill=CARD_BORDER)
        if pct > 0:
            draw.rectangle([x, y, x+max(2, int(w*pct)), y+h], fill=colour)

    def _card(self, draw, x, y, w, h):
        draw.rectangle([x, y, x+w, y+h], fill=CARD_BG, outline=CARD_BORDER)

    def render(self, data, last_refresh, refreshing, next_refresh_time) -> Image.Image:
        img  = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # ── Header ────────────────────────────────────────────────────────
        draw.rectangle([0, 0, W, 22], fill=CARD_BG)
        draw.line([(0, 22), (W, 22)], fill=SEP)
        self._txt(draw, "API DASHBOARD", self.f_sm, TITLE_COL, 6, 4)

        if refreshing:
            self._txt(draw, "↻ refreshing", self.f_xs, WARN, W-4, 6, anchor="ra")
        elif last_refresh:
            self._txt(draw, last_refresh.strftime("upd %H:%M"), self.f_xs,
                      LABEL_COL, W-4, 6, anchor="ra")

        # Progress bar
        elapsed  = next_refresh_time - time.monotonic()
        bar_pct  = 1.0 - max(0.0, min(elapsed, REFRESH_INTERVAL)) / REFRESH_INTERVAL
        draw.rectangle([0, 21, max(1, int(W * bar_pct)), 23], fill=BLUE)

        # ── Cards ──────────────────────────────────────────────────────────
        CARD_Y = 26
        CARD_H = 198
        CARD_W = 150
        self._draw_anthropic(draw,   6, CARD_Y, CARD_W, CARD_H,
                             data.get("anthropic",  {}))
        self._draw_elevenlabs(draw, 164, CARD_Y, CARD_W, CARD_H,
                              data.get("elevenlabs", {}))

        # ── Footer ────────────────────────────────────────────────────────
        fy = H - 14
        draw.line([(0, fy-2), (W, fy-2)], fill=SEP)
        self._txt(draw, "[1] Refresh",   self.f_xs, HINT, 4,   fy)
        self._txt(draw, "[4] Backlight", self.f_xs, HINT, W-4, fy, anchor="ra")

        return img

    def _draw_anthropic(self, draw, x, y, w, h, d):
        self._card(draw, x, y, w, h)

        # Header with live key status dot
        valid = d.get("key_valid")
        dot_c = GOOD if valid else (ERR if valid is False else LABEL_COL)
        self._txt(draw, "◈  ANTHROPIC", self.f_sm, TITLE_COL, x+8, y+6)
        self._txt(draw, "●", self.f_xs, dot_c, x+w-6, y+8, anchor="ra")
        draw.line([(x+4, y+20), (x+w-4, y+20)], fill=SEP)
        cy = y + 28

        # Requests remaining — big
        req_rem = d.get("req_remaining")
        req_lim = d.get("req_limit")
        self._txt(draw, "Requests / min", self.f_xs, LABEL_COL, x+8, cy)
        cy += 13
        if req_lim:
            pct = 1.0 - (req_rem or 0) / req_lim
            c   = pct_colour(pct)
            self._txt(draw, str(req_rem), self.f_xl, c, x+8, cy)
            cy += 26
            self._txt(draw, f"of {req_lim} limit", self.f_xs, LABEL_COL, x+8, cy)
            cy += 13
            self._bar(draw, x+8, cy, w-16, 7, pct, c)
            cy += 14
            self._txt(draw, f"used {req_lim-(req_rem or 0)} ({pct*100:.0f}%)",
                      self.f_xs, LABEL_COL, x+8, cy)
            cy += 16
        else:
            self._txt(draw, "—", self.f_xl, LABEL_COL, x+8, cy)
            cy += 30

        draw.line([(x+4, cy), (x+w-4, cy)], fill=SEP)
        cy += 6

        # Tokens remaining — big
        tok_rem = d.get("tok_remaining")
        tok_lim = d.get("tok_limit")
        self._txt(draw, "Tokens / min", self.f_xs, LABEL_COL, x+8, cy)
        cy += 13
        if tok_lim:
            pct = 1.0 - (tok_rem or 0) / tok_lim
            c   = pct_colour(pct)
            self._txt(draw, fmt_k(tok_rem), self.f_xl, c, x+8, cy)
            cy += 26
            self._txt(draw, f"of {fmt_k(tok_lim)} limit", self.f_xs, LABEL_COL, x+8, cy)
            cy += 13
            self._bar(draw, x+8, cy, w-16, 7, pct, c)
            cy += 14
            self._txt(draw, f"used {fmt_k(tok_lim-(tok_rem or 0))} ({pct*100:.0f}%)",
                      self.f_xs, LABEL_COL, x+8, cy)
        else:
            self._txt(draw, "—", self.f_xl, LABEL_COL, x+8, cy)

        if d.get("error") and not d.get("key_valid"):
            self._txt(draw, d["error"][:16], self.f_xs, ERR, x+8, y+h-12)

    def _draw_elevenlabs(self, draw, x, y, w, h, d):
        self._card(draw, x, y, w, h)
        self._txt(draw, "◎  ELEVENLABS", self.f_sm, TITLE_COL, x+8, y+6)
        draw.line([(x+4, y+20), (x+w-4, y+20)], fill=SEP)
        cy = y + 26

        # Status
        status = d.get("status", "")
        s_col  = GOOD if status == "active" else (WARN if status else LABEL_COL)
        self._txt(draw, f"● {status or '—'}", self.f_xs, s_col, x+8, cy)
        cy += 15

        # Characters
        used  = d.get("chars_used")
        limit = d.get("chars_limit")
        if used is not None and limit and limit > 0:
            remaining = limit - used
            pct       = used / limit
            c         = pct_colour(pct)
            self._txt(draw, "Characters", self.f_xs, LABEL_COL, x+8, cy)
            cy += 13
            self._txt(draw, fmt_k(remaining), self.f_xl, c, x+8, cy)
            cy += 24
            self._txt(draw, f"remaining of {fmt_k(limit)}", self.f_xs, LABEL_COL, x+8, cy)
            cy += 13
            self._bar(draw, x+8, cy, w-16, 6, pct, c)
            cy += 12
            self._txt(draw, f"used {fmt_k(used)} ({pct*100:.0f}%)", self.f_xs,
                      LABEL_COL, x+8, cy)
            cy += 15
        else:
            self._txt(draw, "Characters", self.f_xs, LABEL_COL, x+8, cy)
            cy += 13
            self._txt(draw, "—", self.f_lg, LABEL_COL, x+8, cy)
            cy += 22

        draw.line([(x+4, cy), (x+w-4, cy)], fill=SEP)
        cy += 5

        # Plan
        tier = d.get("tier", "—")
        self._txt(draw, "Plan", self.f_xs, LABEL_COL, x+8, cy)
        self._txt(draw, str(tier).capitalize(), self.f_xs, VALUE_COL, x+w-6, cy, anchor="ra")
        cy += 14

        # Reset
        reset = d.get("next_reset")
        self._txt(draw, "Resets", self.f_xs, LABEL_COL, x+8, cy)
        self._txt(draw, reset or "—", self.f_xs, VALUE_COL, x+w-6, cy, anchor="ra")
        cy += 14

        # Overage
        overage = d.get("overage")
        if overage:
            cur = d.get("overage_currency", "USD")
            self._txt(draw, "Overage", self.f_xs, LABEL_COL, x+8, cy)
            self._txt(draw, f"{fmt_dollars(overage)} {cur}", self.f_xs, WARN,
                      x+w-6, cy, anchor="ra")

        if d.get("error"):
            self._txt(draw, d["error"][:16], self.f_xs, ERR, x+8, y+h-12)


# ── Dashboard ─────────────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self):
        self.renderer      = Renderer()
        self.bpp           = get_fb_info()
        self.data: dict    = {}
        self.last_refresh  = None
        self.refreshing    = False
        self.backlight_on  = True
        self._next_refresh = 0.0
        self._lock         = threading.Lock()
        log.info(f"Framebuffer: {FB_DEV}, {self.bpp}bpp")

    def trigger_refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        threading.Thread(target=self._refresh, daemon=True).start()

    def _refresh(self):
        log.info("Refreshing…")
        try:
            d = fetch_all()
            with self._lock:
                self.data        = d
                self.last_refresh = datetime.now()
            log.info("Done.")
        except Exception as e:
            log.error(f"Refresh error: {e}")
        finally:
            self.refreshing    = False
            self._next_refresh = time.monotonic() + REFRESH_INTERVAL

    def toggle_backlight(self):
        self.backlight_on = not self.backlight_on
        GPIO.output(BACKLIGHT_PIN, GPIO.HIGH if self.backlight_on else GPIO.LOW)

    def run(self):
        setup_gpio()
        log.info("Starting.")
        self.trigger_refresh()
        self._next_refresh = time.monotonic() + REFRESH_INTERVAL

        btn_prev = {BTN_REFRESH: GPIO.HIGH, BTN_BACKLIGHT: GPIO.HIGH}
        DEBOUNCE = 0.05
        interval = 1.0 / FPS

        try:
            while True:
                # Buttons
                for pin, action in ((BTN_REFRESH, self.trigger_refresh),
                                    (BTN_BACKLIGHT, self.toggle_backlight)):
                    state = GPIO.input(pin)
                    if state == GPIO.LOW and btn_prev[pin] == GPIO.HIGH:
                        time.sleep(DEBOUNCE)
                        if GPIO.input(pin) == GPIO.LOW:
                            log.info(f"GPIO{pin} pressed")
                            action()
                    btn_prev[pin] = state

                # Auto-refresh
                if time.monotonic() >= self._next_refresh and not self.refreshing:
                    self.trigger_refresh()

                # Render
                with self._lock:
                    data         = dict(self.data)
                    last_refresh = self.last_refresh
                    refreshing   = self.refreshing

                img = self.renderer.render(data, last_refresh, refreshing,
                                           self._next_refresh)
                image_to_fb(img, FB_DEV, self.bpp)

                time.sleep(interval)

        except KeyboardInterrupt:
            log.info("Exiting.")
        finally:
            cleanup_gpio()


if __name__ == "__main__":
    Dashboard().run()
