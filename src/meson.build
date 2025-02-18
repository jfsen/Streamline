pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'streamline'
gnome = import('gnome')

# Main application resources
gnome.compile_resources('streamline',
  'streamline.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

# Icons resources
gnome.compile_resources('streamline-icons',
  'icons/icons.gresource.xml',
  source_dir: 'icons',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'streamline.in',
  output: 'streamline',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x'
)

streamline_sources = [
  '__init__.py',
  'main.py',
  'window.py',
  'preferences.py',
  'twitch.py',
  'stream_player.py',
  'vod_page.py',
  'icon_names.py',
  'dialogs.py',
  'config.py',
  'rows.py',
  'chat_page.py',
  'emote_cache.py'
]

install_data(streamline_sources, install_dir: moduledir)
