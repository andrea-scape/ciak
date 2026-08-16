project('ciak',
  version: '0.1.5',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 1.0.0'
)

i18n = import('i18n')
i18n.gettext('ciak', args: '--directory=po')

base_id = 'io.github.andrea_scape.ciak'
app_id = base_id

if get_option('profile') == 'development'
  app_id = base_id + '.Devel'
endif

python = import('python')
python_bin = python.find_installation('python3')

gnome = import('gnome')

subdir('data')
subdir('src')
subdir('tests')

fs = import('fs')
if fs.exists('README.md')
  install_data('README.md', install_dir: get_option('datadir') / 'doc' / meson.project_name())
endif

install_data('data/ciak', install_dir: get_option('bindir'), install_mode: 'rwxr-xr-x')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: false,
  update_desktop_database: true,
)
