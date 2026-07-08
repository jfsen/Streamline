
<p align="center">
  <img src="data/icons/hicolor/scalable/apps/org.jfsen.Streamline.svg" alt="Streamline icon" width="128" height="128"/>
</p>

<h1 align="center">Streamline</h1>

<p align="center">
  <strong>Watch Twitch streams in your local media player</strong>
</p>

<p align="center">
  <a href="https://github.com/jfsen/Streamline/blob/main/COPYING"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License: GPL-3.0-or-later"/></a>
  <a href="https://github.com/jfsen/Streamline/releases"><img src="https://img.shields.io/github/v/release/jfsen/Streamline?filter=v*" alt="Release"/></a>
</p>

---

Streamline is a GTK4/libadwaita application that lets you follow your favorite Twitch streamers, check their online status, and watch them in your preferred media player — **mpv**, **VLC**, or a [Streamlink](https://streamlink.github.io/)-compatible custom player — all without a web browser. It also supports browsing and playing channel VODs.

## Features

- **Follow streamers** — Add and unfollow Twitch streamers with a simple dialog
- **Live status** — See who's online and who's offline at a glance
- **Stream playback** — Watch live streams via Streamlink with mpv, VLC, or any custom player
- **VOD browser** — Browse and play past broadcasts (VODs) from followed channels
- **Chat** — Place a chat window next to the stream to see how people react
- **Quick Play** — Watch a one-off stream without following the channel
- **Quality presets** — Choose from High, Medium, Low, or set a custom quality string
- **Backup** — Save your streamer list to a text file

## Screenshots

<p align="center">
  <img src="images/streamline-screenshot.png" alt="Streamline main window"/>
</p>

## Installation

### Flatpak (recommended)

A `.flatpakref` file is attached to each [GitHub release](https://github.com/jfsen/Streamline/releases).
Download the file and install it:

```bash
flatpak install ./org.jfsen.Streamline.flatpakref
```

To build from source instead, use the Flatpak manifest included in the repository:

```bash
flatpak-builder --user --install --force-clean build-dir org.jfsen.Streamline.json
```

The Flatpak bundles all Python dependencies including Streamlink — only a
media player (mpv, VLC, or similar) is needed on the host system.

To use the Twitch API, set your Client ID and Secret from the
[Twitch Developer Console](https://dev.twitch.tv/console) as environment
variables:

```bash
flatpak override --user --env=TWITCH_CLIENT_ID=your_client_id --env=TWITCH_CLIENT_SECRET=your_client_secret org.jfsen.Streamline
```

### Build from source

#### Dependencies

##### Runtime

- Python 3
- [Streamlink](https://streamlink.github.io/install.html)
- A media player (mpv, VLC, or similar)
- GTK 4 and libadwaita
- PyGObject (`python-gobject` / `python3-gi`)
- `python-requests` and `python-pillow`
- Twitch API credentials (Client ID and Secret)

##### Build

- `meson`
- `desktop-file-utils`
- `appstream-glib` (or `appstreamcli`)

##### Install dependencies by distro

**Arch Linux**
```bash
sudo pacman -S --needed meson python python-gobject gtk4 libadwaita \
  python-requests python-pillow streamlink mpv desktop-file-utils appstream-glib
```

**Debian / Ubuntu**
```bash
sudo apt install meson python3 python3-gi gir1.2-gtk-4.0 \
  gir1.2-adw-1 python3-requests python3-pillow streamlink mpv desktop-file-utils appstream
```

**Fedora**
```bash
sudo dnf install meson python3 python3-gobject gtk4 libadwaita \
  python3-requests python3-pillow streamlink mpv desktop-file-utils appstream-glib
```

#### Build and install (system-wide)

```bash
git clone https://github.com/jfsen/Streamline.git
cd Streamline
```

To use the Twitch API , create a local credentials file from the template and fill in your Client ID and Secret from the [Twitch Developer Console](https://dev.twitch.tv/console):

```bash
cp src/config_credentials.example.py src/config_credentials.py
$EDITOR src/config_credentials.py
```

Build and install:

```bash
meson setup builddir --prefix=/usr
meson compile -C builddir
sudo meson install -C builddir
```

For a per-user install, use `--prefix=~/.local` instead of `--prefix=/usr` and
omit `sudo`.

#### Uninstall

```bash
sudo ninja -C builddir uninstall
```

## Keyboard shortcuts

| Shortcut              | Action             |
| --------------------- | ------------------ |
| <kbd>Ctrl</kbd>+<kbd>N</kbd>  | Follow a streamer  |
| <kbd>Ctrl</kbd>+<kbd>P</kbd>  | Quick Play         |
| <kbd>Ctrl</kbd>+<kbd>R</kbd> / <kbd>F5</kbd> | Refresh stream list |
| <kbd>Ctrl</kbd>+<kbd>,</kbd>  | Preferences        |
| <kbd>Ctrl</kbd>+<kbd>Q</kbd>  | Quit               |

## Configuration

All preferences are available under **Preferences** (<kbd>Ctrl</kbd>+<kbd>,</kbd>):

- **Player** — mpv, VLC, or a custom player executable.
  Custom players need to be compatible with Streamlink.
- **Stream quality** — High, Medium, Low, or Custom.
  When set to *Custom*, you can enter a [Streamlink stream type](https://streamlink.github.io/cli.html#cmdoption-stream-types) string.
  Examples: `best` (default), `1080p60`, `720p,720p60`, `audio_only`, `worst`.
- **Theme** — System, Light, Dark, Anthracite, Justin, Oxide
- **Low latency** — Toggle low-latency stream playback.
  May cause issues on slow connections.
- **Chat** — Toggle alternating message backgrounds, animated emotes and message highlights

## License

Streamline is free software licensed under the [GNU General Public License v3.0 or later](COPYING).

## Credits

Created by [jfsen](https://github.com/jfsen).

Powered by [Streamlink](https://streamlink.github.io/), the [Twitch API](https://dev.twitch.tv/docs/api/), and [libadwaita](https://gnome.pages.gitlab.gnome.org/libadwaita/).
