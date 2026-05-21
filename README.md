# PiTFT API Credit Dashboard

A Raspberry Pi dashboard showing live **Anthropic API** and **ElevenLabs**
account status on an Adafruit 2.8" PiTFT (320×240) hat.

```
┌─────────────────────────────────────────────────────────────────┐
│  API DASHBOARD                                      upd 14:22   │
├──────────────────────────────┬──────────────────────────────────┤
│  ◈  ANTHROPIC                │  ◎  ELEVENLABS                   │
│  ● key  active               │  ● active                        │
│                              │                                  │
│  Credit Balance              │  Characters                      │
│  $14.23                      │  82K                             │
│                              │  remaining of 100K               │
│  ───────────────────         │  ████████░░  82%                 │
│  Req / min    45/50          │  used 18K (18%)                  │
│  ████████░░                  │                                  │
│  Tok / min  42K/50K          │  ───────────────────             │
│  ████████░░                  │  Plan      Creator               │
│  Tier         Build          │  Resets    Jun 01                │
└──────────────────────────────┴──────────────────────────────────┘
│  [1] Refresh                                   [4] Backlight    │
└─────────────────────────────────────────────────────────────────┘
```

## What's live vs static

| Field | Source |
|---|---|
| Anthropic key valid/invalid | Live — pinged on every refresh |
| Anthropic requests remaining / limit | Live — from API response headers |
| Anthropic tokens remaining / limit | Live — from API response headers |
| Anthropic credit balance | **Static** — set in `.env`, update after top-ups |
| Anthropic tier | **Static** — set in `.env` |
| ElevenLabs everything | Live — official `/v1/user/subscription` endpoint |

The Anthropic balance being static is a platform limitation — live billing
data requires an Admin API key that Anthropic only issues to org-level
accounts. The rate-limit headers are the genuinely useful live signal anyway:
they tell you how much headroom you have right now.

---

## Hardware

- Raspberry Pi (any model with 40-pin GPIO)
- [Adafruit PiTFT 2.8" 320×240 + Resistive Touchscreen Hat](https://www.adafruit.com/product/2298)

**Button mapping (left → right on the hat):**

| Button | GPIO (BCM) | Function |
|--------|-----------|----------|
| 1 | 17 | Refresh now |
| 2 | 22 | Reserved |
| 3 | 23 | Reserved |
| 4 | 27 | Toggle backlight |

---

## Setup

### 1. Install PiTFT drivers

Follow Adafruit's guide to get the framebuffer working first:
https://learn.adafruit.com/adafruit-pitft-28-inch-resistive-touchscreen-display-raspberry-pi

Run their installer and choose **Console** mode (framebuffer, no X11 needed).
Confirm `/dev/fb1` exists before continuing.

### 2. Clone the repo

```bash
git clone https://github.com/KalinJenkins/token-dashboard.git
cd token-dashboard
```

### 3. Configure .env

```bash
cp .env.example .env
nano .env
```

Fill in the three required values — see **API Keys** below for where to find each.

### 4. Create venv and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> `RPi.GPIO` only installs on a Raspberry Pi. On a dev machine, comment it
> out of `requirements.txt` and stub it, or test directly on the Pi.

### 5. Run

```bash
source venv/bin/activate
python dashboard.py
```

- **Button 1** (GPIO 17) — refresh immediately
- **Button 4** (GPIO 27) — toggle backlight
- On desktop: **R** to refresh, **Q** to quit

---

## Autostart on boot

```bash
# Edit the service file if your username isn't 'pi'
sudo cp pitft-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pitft-dashboard
sudo systemctl start pitft-dashboard

# Check logs
sudo journalctl -u pitft-dashboard -f
```

---

## API Keys

### Anthropic API Key
- Go to **console.anthropic.com** → Settings → API Keys
- Use any existing key, or create a new one
- It only needs to be valid — the dashboard pings `/v1/models` to check
  liveness and read rate-limit headers, which costs nothing

### Anthropic Credit Balance
- Check **console.anthropic.com** → Settings → Billing
- Copy the balance into `ANTHROPIC_CREDIT_BALANCE` in `.env`
- Update it manually after topping up (typically once a month)

### ElevenLabs API Key
- Go to **elevenlabs.io** → click your avatar → Profile + API key
- Copy the key into `ELEVENLABS_API_KEY` in `.env`

---

## .env reference

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-api03-…
ANTHROPIC_CREDIT_BALANCE=14.23      # dollars, update after top-ups
ANTHROPIC_TIER=Build                # shown in Console

ELEVENLABS_API_KEY=…

# Optional
REFRESH_INTERVAL_SECONDS=300        # default: 300 (5 minutes)
```

---

## Project structure

```
token-dashboard/
├── dashboard.py              # Main loop, GPIO, pygame rendering
├── fetchers.py               # API fetchers (Anthropic + ElevenLabs)
├── requirements.txt
├── .env.example              # Template — copy to .env, never commit .env
├── .gitignore                # .env is excluded
├── pitft-dashboard.service   # systemd unit for autostart
└── README.md
```

---

## Troubleshooting

**Black screen**
Confirm `/dev/fb1` exists. The PiTFT driver must be installed first.
Check `SDL_FBDEV` is set to `/dev/fb1` in the service file.

**`RPi.GPIO` import error**
Expected on non-Pi machines. Install `mock-rpi-gpio` for local dev,
or test directly on the Pi.

**Anthropic shows rate limit as `—`**
The rate-limit headers only appear when the API responds. If the key is
invalid you'll see a red dot and no limit data.

**ElevenLabs shows an error**
Double-check `ELEVENLABS_API_KEY` in `.env`. A 401 means the key is wrong
or expired — generate a fresh one from your ElevenLabs profile page.