#!/usr/bin/env python3
"""
PiTFT API Credit Dashboard
Displays Anthropic API, Claude.ai, and ElevenLabs account balances
on an Adafruit 2.8" PiTFT (320x240) with 4-button support.

Button mapping (BCM GPIO):
  GPIO 17 - Button 1 (leftmost)  → Refresh all
  GPIO 22 - Button 2             → (reserved / future use)
  GPIO 23 - Button 3             → (reserved / future use)
  GPIO 27 - Button 4 (rightmost) → Toggle backlight
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

# ── Configuration ────────────────────────────────────────────────────────────
load_dotenv()

REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL_SECONDS", 300))  # 5 min default
DISPLAY_WIDTH = 320
DISPLAY_HEIGHT = 240
FPS = 10

# GPIO button pins (BCM numbering, active-low)
BTN_REFRESH   = 17  # Button 1 – leftmost
BTN_UNUSED_2  = 22  # Button 2
BTN_UNUSED_3  = 23  # Button 3
BTN_BACKLIGHT = 27  # Button 4 – rightmost

BACKLIGHT_PIN = 18  # PWM backlight control

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Colours (dark theme) ─────────────────────────────────────────────────────
C_BG          = (10,  10,  18)
C_CARD_BG     = (20,  22,  35)
C_CARD_BORDER = (40,  44,  70)
C_TITLE       = (160, 165, 210)
C_VALUE       = (240, 242, 255)
C_LABEL       = (100, 105, 140)
C_GOOD        = (80,  200, 120)
C_WARN        = (230, 180,  50)
C_ERROR       = (220,  70,  70)
C_REFRESH_BAR = (50,  120, 200)
C_SEPARATOR   = (35,  38,  58)
C_BTN_HINT    = (60,   65,  95)
C_WHITE       = (255, 255, 255)

# ── Helpers ──────────────────────────────────────────────────────────────────

def fmt_dollars(val):
    """Format a float as $X.XX or return val unchanged if it's a string."""
    if isinstance(val, (int, float)):
        return f"${val:,.2f}"
    return str(val)

def fmt_number(val):
    if isinstance(val, (int, float)):
        return f"{val:,.0f}"
    return str(val)

def status_colour(val, warn_threshold=None, error_threshold=None):
    """Pick a display colour based on numeric thresholds."""
    if not isinstance(val, (int, float)):
        return C_ERROR
    if error_threshold is not None and val <= error_threshold:
        return C_ERROR
    if warn_threshold is not None and val <= warn_threshold:
        return C_WARN
    return C_GOOD


# ── GPIO Setup ───────────────────────────────────────────────────────────────

def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in (BTN_REFRESH, BTN_UNUSED_2, BTN_UNUSED_3, BTN_BACKLIGHT):
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    # Backlight pin as output
    GPIO.setup(BACKLIGHT_PIN, GPIO.OUT)
    GPIO.output(BACKLIGHT_PIN, GPIO.HIGH)

def cleanup_gpio():
    GPIO.cleanup()


# ── Dashboard UI ─────────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self):
        pygame.init()

        # Try to use the PiTFT framebuffer; fall back to a window for dev
        if os.path.exists("/dev/fb1"):
            os.environ.setdefault("SDL_FBDEV", "/dev/fb1")
            os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
            os.environ.setdefault("SDL_MOUSEDRV", "TSLIB")
            os.environ.setdefault("SDL_MOUSEDEV", "/dev/input/touchscreen")

        self.screen = pygame.display.set_mode((DISPLAY_WIDTH, DISPLAY_HEIGHT))
        pygame.display.set_caption("API Dashboard")
        pygame.mouse.set_visible(False)

        self.clock = pygame.time.Clock()

        # Fonts
        self.font_lg  = pygame.font.SysFont("dejavusansmono", 18, bold=True)
        self.font_md  = pygame.font.SysFont("dejavusansmono", 13)
        self.font_sm  = pygame.font.SysFont("dejavusansmono", 11)
        self.font_xs  = pygame.font.SysFont("dejavusansmono", 10)

        self.data: dict = {}
        self.last_refresh: datetime | None = None
        self.refreshing: bool = False
        self.error_msg: str | None = None
        self.backlight_on: bool = True

        self._next_refresh_time: float = 0.0
        self._lock = threading.Lock()

    # ── Data fetching ─────────────────────────────────────────────────────

    def trigger_refresh(self):
        """Kick off a background refresh thread."""
        if self.refreshing:
            return
        self.refreshing = True
        t = threading.Thread(target=self._do_refresh, daemon=True)
        t.start()

    def _do_refresh(self):
        log.info("Refreshing data…")
        try:
            new_data = fetch_all()
            with self._lock:
                self.data = new_data
                self.last_refresh = datetime.now()
                self.error_msg = None
            log.info("Refresh complete.")
        except Exception as exc:
            log.error(f"Refresh failed: {exc}")
            with self._lock:
                self.error_msg = str(exc)
        finally:
            self.refreshing = False
            self._next_refresh_time = time.monotonic() + REFRESH_INTERVAL

    # ── Drawing helpers ───────────────────────────────────────────────────

    def _text(self, surf, text, font, colour, x, y, anchor="topleft"):
        img = font.render(str(text), True, colour)
        r = img.get_rect(**{anchor: (x, y)})
        surf.blit(img, r)
        return r

    def _card(self, surf, x, y, w, h):
        rect = pygame.Rect(x, y, w, h)
        pygame.draw.rect(surf, C_CARD_BG, rect, border_radius=6)
        pygame.draw.rect(surf, C_CARD_BORDER, rect, width=1, border_radius=6)
        return rect

    # ── Layout drawing ────────────────────────────────────────────────────

    def draw(self):
        surf = self.screen
        surf.fill(C_BG)

        with self._lock:
            data = dict(self.data)
            last_refresh = self.last_refresh
            refreshing = self.refreshing
            error_msg = self.error_msg

        # ── Header bar ────────────────────────────────────────────────────
        pygame.draw.rect(surf, C_CARD_BG, (0, 0, DISPLAY_WIDTH, 22))
        pygame.draw.line(surf, C_SEPARATOR, (0, 22), (DISPLAY_WIDTH, 22))
        self._text(surf, "API CREDIT DASHBOARD", self.font_sm, C_TITLE, 6, 4)

        if refreshing:
            self._text(surf, "↻ refreshing…", self.font_xs, C_WARN,
                       DISPLAY_WIDTH - 4, 6, anchor="topright")
        elif last_refresh:
            ts = last_refresh.strftime("%H:%M")
            self._text(surf, f"upd {ts}", self.font_xs, C_LABEL,
                       DISPLAY_WIDTH - 4, 6, anchor="topright")

        # ── Auto-refresh progress bar ─────────────────────────────────────
        elapsed = self._next_refresh_time - time.monotonic()
        elapsed = max(0.0, min(elapsed, REFRESH_INTERVAL))
        bar_frac = 1.0 - (elapsed / REFRESH_INTERVAL)
        bar_w = int(DISPLAY_WIDTH * bar_frac)
        pygame.draw.rect(surf, C_REFRESH_BAR, (0, 21, bar_w, 2))

        y_start = 28

        if error_msg and not data:
            self._text(surf, "Fetch error:", self.font_md, C_ERROR, 8, y_start + 10)
            self._text(surf, error_msg[:38], self.font_xs, C_LABEL, 8, y_start + 30)
            self._draw_footer(surf)
            pygame.display.flip()
            return

        # ── Three-column card grid ─────────────────────────────────────────
        #  Col:   Anthropic API | Claude.ai | ElevenLabs
        #  Row 2: (span all)  last-error if any
        CARD_W  = 100
        CARD_H  = 175
        GUTTER  = 5
        total_w = 3 * CARD_W + 2 * GUTTER
        x0 = (DISPLAY_WIDTH - total_w) // 2

        self._draw_anthropic_card(surf, x0,              y_start, CARD_W, CARD_H,
                                  data.get("anthropic", {}))
        self._draw_claude_card   (surf, x0 + CARD_W + GUTTER,
                                                          y_start, CARD_W, CARD_H,
                                  data.get("claude_ai", {}))
        self._draw_elevenlabs_card(surf,x0 + 2*(CARD_W + GUTTER),
                                                          y_start, CARD_W, CARD_H,
                                  data.get("elevenlabs", {}))

        self._draw_footer(surf)
        pygame.display.flip()

    def _draw_card_header(self, surf, x, y, w, title, icon):
        self._text(surf, f"{icon} {title}", self.font_sm, C_TITLE, x + 5, y + 5)
        pygame.draw.line(surf, C_SEPARATOR, (x + 3, y + 19), (x + w - 3, y + 19))

    def _draw_anthropic_card(self, surf, x, y, w, h, d):
        self._card(surf, x, y, w, h)
        self._draw_card_header(surf, x, y, w, "Anthropic", "◈")
        cy = y + 24

        # Credit balance
        balance = d.get("credit_balance")
        colour  = status_colour(balance, warn_threshold=5.0, error_threshold=1.0)
        self._text(surf, "Balance", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        self._text(surf, fmt_dollars(balance) if balance is not None else "—",
                   self.font_md, colour, x+5, cy)
        cy += 18

        # Monthly usage
        usage = d.get("monthly_usage")
        self._text(surf, "This mo.", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        self._text(surf, fmt_dollars(usage) if usage is not None else "—",
                   self.font_md, C_VALUE, x+5, cy)
        cy += 18

        # Tier / plan
        tier = d.get("tier", "—")
        self._text(surf, "Tier", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        self._text(surf, str(tier)[:11], self.font_xs, C_VALUE, x+5, cy)
        cy += 16

        # Rate limit info
        rpm = d.get("requests_per_minute")
        if rpm is not None:
            self._text(surf, f"RPM {rpm}", self.font_xs, C_LABEL, x+5, cy)
            cy += 13

        if d.get("error"):
            self._text(surf, "err", self.font_xs, C_ERROR, x+5, y+h-14)

    def _draw_claude_card(self, surf, x, y, w, h, d):
        self._card(surf, x, y, w, h)
        self._draw_card_header(surf, x, y, w, "Claude.ai", "◉")
        cy = y + 24

        plan = d.get("plan", "—")
        self._text(surf, "Plan", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        self._text(surf, str(plan)[:11], self.font_md, C_VALUE, x+5, cy)
        cy += 18

        # Usage
        used = d.get("messages_used")
        limit = d.get("messages_limit")
        self._text(surf, "Used", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        if used is not None and limit is not None:
            pct = used / limit if limit else 0
            colour = C_ERROR if pct > 0.9 else (C_WARN if pct > 0.7 else C_GOOD)
            self._text(surf, f"{used}/{limit}", self.font_xs, colour, x+5, cy)
            cy += 13
            # Mini progress bar
            BAR_W = w - 10
            pygame.draw.rect(surf, C_CARD_BORDER, (x+5, cy, BAR_W, 5), border_radius=2)
            fill = max(1, int(BAR_W * pct))
            pygame.draw.rect(surf, colour, (x+5, cy, fill, 5), border_radius=2)
            cy += 12
        elif used is not None:
            self._text(surf, fmt_number(used), self.font_xs, C_VALUE, x+5, cy)
            cy += 13
        else:
            self._text(surf, "—", self.font_xs, C_LABEL, x+5, cy)
            cy += 13

        # Resets
        resets = d.get("resets_at")
        if resets:
            self._text(surf, "Resets", self.font_xs, C_LABEL, x+5, cy)
            cy += 12
            self._text(surf, str(resets)[:11], self.font_xs, C_VALUE, x+5, cy)
            cy += 12

        if d.get("error"):
            self._text(surf, "err", self.font_xs, C_ERROR, x+5, y+h-14)

    def _draw_elevenlabs_card(self, surf, x, y, w, h, d):
        self._card(surf, x, y, w, h)
        self._draw_card_header(surf, x, y, w, "11Labs", "◎")
        cy = y + 24

        # Character quota
        used = d.get("chars_used")
        limit = d.get("chars_limit")
        self._text(surf, "Chars", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        if used is not None and limit is not None:
            pct = used / limit if limit else 0
            colour = C_ERROR if pct > 0.9 else (C_WARN if pct > 0.7 else C_GOOD)
            remaining = limit - used
            self._text(surf, fmt_number(remaining), self.font_md, colour, x+5, cy)
            cy += 16
            self._text(surf, "left", self.font_xs, C_LABEL, x+5, cy)
            cy += 13
            # Mini progress bar
            BAR_W = w - 10
            pygame.draw.rect(surf, C_CARD_BORDER, (x+5, cy, BAR_W, 5), border_radius=2)
            fill = max(1, int(BAR_W * pct))
            pygame.draw.rect(surf, colour, (x+5, cy, fill, 5), border_radius=2)
            cy += 12
        else:
            self._text(surf, "—", self.font_md, C_LABEL, x+5, cy)
            cy += 18

        # Plan / tier
        tier = d.get("tier", "—")
        self._text(surf, "Plan", self.font_xs, C_LABEL, x+5, cy)
        cy += 13
        self._text(surf, str(tier)[:11], self.font_xs, C_VALUE, x+5, cy)
        cy += 16

        # Next reset
        resets = d.get("next_reset")
        if resets:
            self._text(surf, "Resets", self.font_xs, C_LABEL, x+5, cy)
            cy += 12
            self._text(surf, str(resets)[:11], self.font_xs, C_VALUE, x+5, cy)
            cy += 12

        if d.get("error"):
            self._text(surf, "err", self.font_xs, C_ERROR, x+5, y+h-14)

    def _draw_footer(self, surf):
        y = DISPLAY_HEIGHT - 16
        pygame.draw.line(surf, C_SEPARATOR, (0, y - 2), (DISPLAY_WIDTH, y - 2))
        self._text(surf, "[1] Refresh", self.font_xs, C_BTN_HINT, 4, y)
        self._text(surf, "[4] Backlight", self.font_xs, C_BTN_HINT, DISPLAY_WIDTH - 4, y,
                   anchor="topright")

    # ── Toggle backlight ──────────────────────────────────────────────────

    def toggle_backlight(self):
        self.backlight_on = not self.backlight_on
        GPIO.output(BACKLIGHT_PIN, GPIO.HIGH if self.backlight_on else GPIO.LOW)

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self):
        setup_gpio()
        log.info("Starting dashboard.")
        self.trigger_refresh()
        self._next_refresh_time = time.monotonic() + REFRESH_INTERVAL

        btn_last = {
            BTN_REFRESH:   GPIO.HIGH,
            BTN_UNUSED_2:  GPIO.HIGH,
            BTN_UNUSED_3:  GPIO.HIGH,
            BTN_BACKLIGHT: GPIO.HIGH,
        }
        DEBOUNCE = 0.05  # seconds

        try:
            while True:
                # ── pygame events ──────────────────────────────────────────
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        return
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_r:
                            self.trigger_refresh()
                        elif event.key == pygame.K_q:
                            return

                # ── GPIO button polling (debounce) ─────────────────────────
                for pin, action in (
                    (BTN_REFRESH,   self.trigger_refresh),
                    (BTN_BACKLIGHT, self.toggle_backlight),
                ):
                    state = GPIO.input(pin)
                    if state == GPIO.LOW and btn_last[pin] == GPIO.HIGH:
                        time.sleep(DEBOUNCE)
                        if GPIO.input(pin) == GPIO.LOW:
                            log.info(f"Button GPIO{pin} pressed.")
                            action()
                    btn_last[pin] = state

                # ── Auto-refresh ───────────────────────────────────────────
                if time.monotonic() >= self._next_refresh_time and not self.refreshing:
                    self.trigger_refresh()

                self.draw()
                self.clock.tick(FPS)

        finally:
            cleanup_gpio()
            pygame.quit()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dash = Dashboard()
    dash.run()
