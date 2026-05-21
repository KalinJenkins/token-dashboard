#!/usr/bin/env python3
"""
PiTFT API Credit Dashboard  –  Anthropic + ElevenLabs
320×240  Adafruit 2.8" PiTFT hat

Button mapping (BCM GPIO, active-low):
  GPIO 17  Button 1 (leftmost)  → Refresh now
  GPIO 22  Button 2             → (reserved)
  GPIO 23  Button 3             → (reserved)
  GPIO 27  Button 4 (rightmost) → Toggle backlight
"""

import os
import sys
import time
import threading
import logging
from datetime import datetime

import pygame
import RPi.GPIO as GPIO
from dotenv import load_dotenv

from fetchers import fetch_all

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL_SECONDS", 300))
W, H = 320, 240
FPS  = 10

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

# ── Palette ───────────────────────────────────────────────────────────────────
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
HINT        = (55,   60,  90)

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_dollars(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"

def fmt_k(v):
    """Format large integers as e.g. 1.2M or 45K."""
    if not isinstance(v, (int, float)):
        return "—"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return str(int(v))

def pct_colour(pct):
    if pct >= 0.9: return ERR
    if pct >= 0.7: return WARN
    return GOOD

def val_colour(v, warn=5.0, err=1.0):
    if not isinstance(v, (int, float)): return ERR
    if v <= err: return ERR
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


# ── Dashboard ─────────────────────────────────────────────────────────────────
class Dashboard:
    def __init__(self):
        pygame.init()

        if os.path.exists("/dev/fb1"):
            os.environ.setdefault("SDL_FBDEV", "/dev/fb1")
            os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
            os.environ.setdefault("SDL_MOUSEDRV", "TSLIB")
            os.environ.setdefault("SDL_MOUSEDEV", "/dev/input/touchscreen")

        self.screen = pygame.display.set_mode((W, H))
        pygame.display.set_caption("API Dashboard")
        pygame.mouse.set_visible(False)
        self.clock = pygame.time.Clock()

        # Fonts
        mono = "dejavusansmono"
        self.f_xl  = pygame.font.SysFont(mono, 22, bold=True)
        self.f_lg  = pygame.font.SysFont(mono, 17, bold=True)
        self.f_md  = pygame.font.SysFont(mono, 13)
        self.f_sm  = pygame.font.SysFont(mono, 11)
        self.f_xs  = pygame.font.SysFont(mono, 10)

        self.data: dict            = {}
        self.last_refresh: datetime | None = None
        self.refreshing            = False
        self.backlight_on          = True
        self._next_refresh         = 0.0
        self._lock                 = threading.Lock()

    # ── Data ──────────────────────────────────────────────────────────────────
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
                self.data         = d
                self.last_refresh = datetime.now()
            log.info("Done.")
        except Exception as e:
            log.error(f"Refresh error: {e}")
        finally:
            self.refreshing      = False
            self._next_refresh   = time.monotonic() + REFRESH_INTERVAL

    # ── Drawing helpers ───────────────────────────────────────────────────────
    def _txt(self, surf, text, font, colour, x, y, anchor="topleft"):
        img = font.render(str(text), True, colour)
        r   = img.get_rect(**{anchor: (x, y)})
        surf.blit(img, r)
        return r

    def _bar(self, surf, x, y, w, h, pct, colour):
        pct = max(0.0, min(1.0, pct))
        pygame.draw.rect(surf, CARD_BORDER, (x, y, w, h), border_radius=2)
        if pct > 0:
            pygame.draw.rect(surf, colour, (x, y, max(2, int(w * pct)), h),
                             border_radius=2)

    def _card(self, surf, x, y, w, h):
        pygame.draw.rect(surf, CARD_BG,     (x, y, w, h), border_radius=8)
        pygame.draw.rect(surf, CARD_BORDER, (x, y, w, h), width=1, border_radius=8)

    def _section(self, surf, x, y, w, label, value, vcolour=None,
                 sub=None, sub_colour=None):
        """Draw a label / value pair with optional sub-line. Returns new y."""
        self._txt(surf, label, self.f_xs, LABEL_COL, x, y)
        y += 13
        self._txt(surf, value, self.f_lg, vcolour or VALUE_COL, x, y)
        y += 20
        if sub is not None:
            self._txt(surf, sub, self.f_xs, sub_colour or LABEL_COL, x, y)
            y += 14
        return y

    # ── Cards ─────────────────────────────────────────────────────────────────
    CARD_X  = [6, 164]          # left edges of the two cards
    CARD_W  = 150
    CARD_Y  = 26                # top of cards (below header)
    CARD_H  = 198

    def _draw_anthropic(self, surf, x, y, w, h, d):
        self._card(surf, x, y, w, h)

        # ── Header ────────────────────────────────────────────────────────
        self._txt(surf, "◈  ANTHROPIC", self.f_sm, TITLE_COL, x+8, y+6)
        pygame.draw.line(surf, SEP, (x+4, y+21), (x+w-4, y+21))

        cy = y + 27

        # Key status indicator
        valid = d.get("key_valid")
        if valid is True:
            dot, dot_c = "●", GOOD
        elif valid is False:
            dot, dot_c = "●", ERR
        else:
            dot, dot_c = "●", LABEL_COL
        self._txt(surf, dot + " key", self.f_xs, dot_c, x+8, cy)
        cy += 16

        # Credit balance (static)
        balance = d.get("credit_balance")
        cy = self._section(surf, x+8, cy, w-16,
                           "Credit Balance",
                           fmt_dollars(balance),
                           vcolour=val_colour(balance, warn=5.0, err=1.0))

        pygame.draw.line(surf, SEP, (x+4, cy), (x+w-4, cy))
        cy += 6

        # Rate limits — requests
        req_rem = d.get("req_remaining")
        req_lim = d.get("req_limit")
        if req_lim:
            pct = 1.0 - (req_rem or 0) / req_lim
            c   = pct_colour(pct)
            self._txt(surf, "Req / min", self.f_xs, LABEL_COL, x+8, cy)
            self._txt(surf, f"{req_rem}/{req_lim}", self.f_xs, c, x+w-8, cy,
                      anchor="topright")
            cy += 13
            self._bar(surf, x+8, cy, w-16, 5, pct, c)
            cy += 12
        else:
            self._txt(surf, "RPM  —", self.f_xs, LABEL_COL, x+8, cy)
            cy += 18

        # Rate limits — tokens
        tok_rem = d.get("tok_remaining")
        tok_lim = d.get("tok_limit")
        if tok_lim:
            pct = 1.0 - (tok_rem or 0) / tok_lim
            c   = pct_colour(pct)
            self._txt(surf, "Tok / min", self.f_xs, LABEL_COL, x+8, cy)
            self._txt(surf, f"{fmt_k(tok_rem)}/{fmt_k(tok_lim)}", self.f_xs, c,
                      x+w-8, cy, anchor="topright")
            cy += 13
            self._bar(surf, x+8, cy, w-16, 5, pct, c)
            cy += 12
        else:
            self._txt(surf, "TPM  —", self.f_xs, LABEL_COL, x+8, cy)
            cy += 18

        # Tier
        tier = d.get("tier", "—")
        self._txt(surf, "Tier", self.f_xs, LABEL_COL, x+8, cy)
        self._txt(surf, str(tier), self.f_xs, VALUE_COL, x+w-8, cy, anchor="topright")

        # Error badge
        if d.get("error") and not d.get("key_valid"):
            self._txt(surf, d["error"][:18], self.f_xs, ERR, x+8, y+h-14)

    def _draw_elevenlabs(self, surf, x, y, w, h, d):
        self._card(surf, x, y, w, h)

        # ── Header ────────────────────────────────────────────────────────
        self._txt(surf, "◎  ELEVENLABS", self.f_sm, TITLE_COL, x+8, y+6)
        pygame.draw.line(surf, SEP, (x+4, y+21), (x+w-4, y+21))

        cy = y + 27

        # Status dot
        status = d.get("status", "")
        s_col  = GOOD if status == "active" else (WARN if status else LABEL_COL)
        self._txt(surf, f"● {status or '—'}", self.f_xs, s_col, x+8, cy)
        cy += 16

        # Character usage
        used  = d.get("chars_used")
        limit = d.get("chars_limit")

        if used is not None and limit and limit > 0:
            remaining = limit - used
            pct       = used / limit
            c         = pct_colour(pct)

            self._txt(surf, "Characters", self.f_xs, LABEL_COL, x+8, cy)
            cy += 13
            self._txt(surf, fmt_k(remaining), self.f_xl, c, x+8, cy)
            cy += 26
            self._txt(surf, f"remaining of {fmt_k(limit)}", self.f_xs,
                      LABEL_COL, x+8, cy)
            cy += 13
            self._bar(surf, x+8, cy, w-16, 7, pct, c)
            cy += 14
            self._txt(surf, f"used {fmt_k(used)} ({pct*100:.0f}%)",
                      self.f_xs, LABEL_COL, x+8, cy)
            cy += 16
        else:
            cy = self._section(surf, x+8, cy, w-16, "Characters", "—")

        pygame.draw.line(surf, SEP, (x+4, cy), (x+w-4, cy))
        cy += 6

        # Tier
        tier = d.get("tier", "—")
        self._txt(surf, "Plan", self.f_xs, LABEL_COL, x+8, cy)
        self._txt(surf, str(tier).capitalize(), self.f_xs, VALUE_COL,
                  x+w-8, cy, anchor="topright")
        cy += 15

        # Reset date
        reset = d.get("next_reset")
        self._txt(surf, "Resets", self.f_xs, LABEL_COL, x+8, cy)
        self._txt(surf, reset or "—", self.f_xs, VALUE_COL,
                  x+w-8, cy, anchor="topright")
        cy += 15

        # Overage
        overage = d.get("overage")
        if overage:
            cur = d.get("overage_currency", "USD")
            self._txt(surf, "Overage", self.f_xs, LABEL_COL, x+8, cy)
            self._txt(surf, f"{fmt_dollars(overage)} {cur}", self.f_xs, WARN,
                      x+w-8, cy, anchor="topright")

        if d.get("error"):
            self._txt(surf, d["error"][:18], self.f_xs, ERR, x+8, y+h-14)

    # ── Main draw ─────────────────────────────────────────────────────────────
    def draw(self):
        surf = self.screen
        surf.fill(BG)

        with self._lock:
            data         = dict(self.data)
            last_refresh = self.last_refresh
            refreshing   = self.refreshing

        # ── Header bar ────────────────────────────────────────────────────
        pygame.draw.rect(surf, CARD_BG, (0, 0, W, 24))
        pygame.draw.line(surf, SEP, (0, 24), (W, 24))
        self._txt(surf, "API DASHBOARD", self.f_sm, TITLE_COL, 6, 5)

        if refreshing:
            self._txt(surf, "↻ refreshing", self.f_xs, WARN, W-4, 7, anchor="topright")
        elif last_refresh:
            self._txt(surf, last_refresh.strftime("upd %H:%M"), self.f_xs,
                      LABEL_COL, W-4, 7, anchor="topright")

        # Auto-refresh progress bar (fills left→right as next refresh approaches)
        elapsed  = self._next_refresh - time.monotonic()
        bar_pct  = 1.0 - max(0.0, min(elapsed, REFRESH_INTERVAL)) / REFRESH_INTERVAL
        pygame.draw.rect(surf, BLUE, (0, 23, max(1, int(W * bar_pct)), 2))

        # ── Two cards ─────────────────────────────────────────────────────
        cx, cy, cw, ch = self.CARD_X[0], self.CARD_Y, self.CARD_W, self.CARD_H
        self._draw_anthropic(surf,  cx,          cy, cw, ch, data.get("anthropic",  {}))
        self._draw_elevenlabs(surf, self.CARD_X[1], cy, cw, ch, data.get("elevenlabs", {}))

        # ── Footer ────────────────────────────────────────────────────────
        fy = H - 14
        pygame.draw.line(surf, SEP, (0, fy-2), (W, fy-2))
        self._txt(surf, "[1] Refresh", self.f_xs, HINT, 4, fy)
        self._txt(surf, "[4] Backlight", self.f_xs, HINT, W-4, fy, anchor="topright")

        pygame.display.flip()

    # ── Backlight ─────────────────────────────────────────────────────────────
    def toggle_backlight(self):
        self.backlight_on = not self.backlight_on
        GPIO.output(BACKLIGHT_PIN, GPIO.HIGH if self.backlight_on else GPIO.LOW)

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        setup_gpio()
        log.info("Starting.")
        self.trigger_refresh()
        self._next_refresh = time.monotonic() + REFRESH_INTERVAL

        btn_prev = {BTN_REFRESH: GPIO.HIGH, BTN_BACKLIGHT: GPIO.HIGH}
        DEBOUNCE = 0.05

        try:
            while True:
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        return
                    if ev.type == pygame.KEYDOWN:
                        if ev.key == pygame.K_r: self.trigger_refresh()
                        if ev.key == pygame.K_q: return

                for pin, action in ((BTN_REFRESH, self.trigger_refresh),
                                    (BTN_BACKLIGHT, self.toggle_backlight)):
                    state = GPIO.input(pin)
                    if state == GPIO.LOW and btn_prev[pin] == GPIO.HIGH:
                        time.sleep(DEBOUNCE)
                        if GPIO.input(pin) == GPIO.LOW:
                            log.info(f"GPIO{pin} pressed")
                            action()
                    btn_prev[pin] = state

                if time.monotonic() >= self._next_refresh and not self.refreshing:
                    self.trigger_refresh()

                self.draw()
                self.clock.tick(FPS)
        finally:
            cleanup_gpio()
            pygame.quit()


if __name__ == "__main__":
    Dashboard().run()