# PiTFT API Credit Dashboard

A Raspberry Pi dashboard that shows your **Anthropic API**, **Claude.ai**, and **ElevenLabs**
credit/usage on an Adafruit 2.8" PiTFT (320×240) hat.

```
┌──────────────────────────────────────────────────────┐
│  API CREDIT DASHBOARD                      upd 14:22 │
├──────────────────────────────────────────────────────┤
│  ◈ Anthropic  │  ◉ Claude.ai  │  ◎ 11Labs          │
│  Balance       │  Plan         │  Chars              │
│  $12.34        │  Pro          │  42,000             │
│  This mo.      │  Used         │  left               │
│  $3.10         │  150/1000     │  ████░░░░           │
│  Tier          │  ██░░░░░░░░   │  Plan               │
│  build-4       │  Resets       │  creator            │
│                │  2026-06-01   │  Resets             │
│                │               │  2026-06-01         │
├──────────────────────────────────────────────────────┤
│  [1] Refresh                       [4] Backlight     │
└──────────────────────────────────────────────────────┘
```

## Hardware

- Raspberry Pi (any model with 40-pin GPIO)
- [Adafruit PiTFT 2.8" 320×240 TFT + Touchscreen Hat](https://www.adafruit.com/product/2298)

**Button mapping (left → right on the hat):**

| Button | GPIO | Function |
|--------|------|----------|
| 1      | 17   | Refresh now |
| 2      | 22   | (reserved) |
| 3      | 23   | (reserved) |
| 4      | 27   | Toggle backlight |

---

## Quick start

### 1. Install PiTFT drivers

Follow Adafruit's official guide to get the framebuffer working first:
<https://learn.adafruit.com/adafruit-pitft-28-inch-resistive-touchscreen-display-raspberry-pi>

Run their installer script and choose **"Console"** mode (framebuffer, no X11 needed).

### 2. Clone & configure

```bash
git clone https://github.com/YOUR_USERNAME/pitft-dashboard.git
cd pitft-dashboard

# Copy the example env file and fill in your keys
cp .env.example .env
nano .env
```

See `.env.example` for where to find each key.

### 3. Create virtual environment & install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note:** `RPi.GPIO` only installs on a Raspberry Pi. On your dev machine you can
> comment it out of `requirements.txt` and install `mock-rpi-gpio` for testing.

### 4. Run manually

```bash
source venv/bin/activate
python dashboard.py
```

Press **Button 1** (GPIO 17) to refresh, **Button 4** (GPIO 27) to toggle the backlight.
On a desktop dev machine: press **R** to refresh, **Q** to quit.

### 5. Auto-start on boot (systemd)

```bash
# Adjust paths in the .service file if your username isn't 'pi'
sudo cp pitft-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pitft-dashboard
sudo systemctl start pitft-dashboard

# Check status
sudo systemctl status pitft-dashboard
sudo journalctl -u pitft-dashboard -f
```

---

## API Keys

### Anthropic Admin Key
- Go to <https://console.anthropic.com> → Settings → API keys
- Create an **Admin** key (starts with `sk-ant-admin-…`)
- A regular API key **will not** have access to billing endpoints

### Claude.ai Session Cookie
Claude.ai has no public API for quota data. The dashboard reads it via an internal
endpoint using your browser session cookie:

1. Log into <https://claude.ai> in Chrome or Firefox
2. Open DevTools (`F12`) → **Application** tab → **Cookies** → `https://claude.ai`
3. Find the `__session` cookie and copy its **Value**
4. Paste it into `CLAUDE_SESSION_KEY` in your `.env`

> ⚠️ Session cookies expire. If Claude.ai shows `session expired`, repeat the steps above.

### ElevenLabs API Key
- Go to <https://elevenlabs.io> → click your avatar → **Profile**
- Copy your **API Key**

---

## Customisation

| Setting | Where | Default |
|---------|-------|---------|
| Refresh interval | `REFRESH_INTERVAL_SECONDS` in `.env` | `300` (5 min) |
| Warn threshold (Anthropic balance) | `fetchers.py` → `fetch_anthropic` | $5.00 |
| Error threshold (Anthropic balance) | same | $1.00 |

---

## Project structure

```
pitft-dashboard/
├── dashboard.py          # Main loop, GPIO, pygame rendering
├── fetchers.py           # API fetchers for each service
├── requirements.txt
├── .env.example          # Template — copy to .env, never commit .env
├── .gitignore
├── pitft-dashboard.service  # systemd unit for autostart
└── README.md
```

---

## Troubleshooting

**Black screen / no display**
- Make sure the PiTFT driver is installed and `/dev/fb1` exists: `ls -la /dev/fb*`
- Check `SDL_FBDEV` is set to `/dev/fb1` in the service file

**`RPi.GPIO` import error on a non-Pi machine**
- Expected – GPIO is Pi-only. Use a mock library for dev, or test directly on the Pi.

**Claude.ai shows `session expired`**
- Grab a fresh `__session` cookie from your browser (see above)

**Anthropic balance shows `HTTP 403`**
- You're using a regular API key. You need a Console Admin key.
