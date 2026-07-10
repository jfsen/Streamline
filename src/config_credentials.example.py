"""Twitch API credentials for Streamline — never commit this file.

There are two ways to provide credentials.  Environment variables are
recommended because they work everywhere (source builds, Flatpak, dev):

    export STREAMLINE_TWITCH_CLIENT_ID=your_client_id
    export STREAMLINE_TWITCH_CLIENT_SECRET=your_client_secret

If you prefer a file (source builds only), copy this file to
config_credentials.py and fill in your keys.  It is gitignored:

    $ cp src/config_credentials.example.py src/config_credentials.py
    $ $EDITOR src/config_credentials.py
"""

# ── Twitch API ──────────────────────────────────────────────
# Get your credentials from https://dev.twitch.tv/console/apps
# Create an application there and copy the Client ID and Client
# Secret into the variables below.

TWITCH_CLIENT_ID = ""
TWITCH_CLIENT_SECRET = ""
