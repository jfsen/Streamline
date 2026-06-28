"""Local overrides for config.py — never commit this file.

Copy this file to config_credentials.py (in this same directory) and fill in
your credentials.  config_credentials.py is listed in .gitignore so you can
safely keep your real keys here without accidentally committing them.

Example:

    $ cp src/config_credentials.example.py src/config_credentials.py
    $ $EDITOR src/config_credentials.py
"""

# ── Twitch API ──────────────────────────────────────────────
# Get your credentials from https://dev.twitch.tv/console/apps
# Create an application there and copy the Client ID and Client
# Secret into the variables below.

TWITCH_CLIENT_ID = ""
TWITCH_CLIENT_SECRET = ""
