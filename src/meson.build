conf = configuration_data()
conf.set('APP_ID', app_id)
conf.set('APP_VERSION', meson.project_version())

config_py = configure_file(
  input: 'config.py.in',
  output: 'config.py',
  configuration: conf,
  install: true,
  install_dir: python_bin.get_install_dir() / 'ciak'
)

python_bin.install_sources(
  '__init__.py',
  '__main__.py',
  'main.py',
  'theme.py',
  'icon_theme.py',
  'poster_cache.py',
  'threads.py',
  'window.py',
  'style.css',
  subdir: 'ciak'
)

install_subdir('domain', install_dir: python_bin.get_install_dir() / 'ciak')
install_subdir('data', install_dir: python_bin.get_install_dir() / 'ciak')
install_subdir('ui', install_dir: python_bin.get_install_dir() / 'ciak')
