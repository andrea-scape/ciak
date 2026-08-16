conf = configuration_data()
conf.set('appid', app_id)
if app_id.endswith('.Devel')
  conf.set('app_name', 'Ciak (Development)')
else
  conf.set('app_name', 'Ciak')
endif

desktop_file = configure_file(
  input: 'io.github.andrea_scape.ciak.desktop.in',
  output: '@0@.desktop'.format(app_id),
  configuration: conf,
  install_dir: get_option('datadir') / 'applications'
)

metainfo_file = configure_file(
  input: 'io.github.andrea_scape.ciak.metainfo.xml.in',
  output: '@0@.metainfo.xml'.format(app_id),
  configuration: conf,
  install_dir: get_option('datadir') / 'metainfo'
)

gschema_file = configure_file(
  input: 'io.github.andrea_scape.ciak.gschema.xml',
  output: '@0@.gschema.xml'.format(app_id),
  configuration: conf,
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas'
)

install_data(
  'icons/hicolor/scalable/apps/io.github.andrea_scape.ciak.svg',
  rename: '@0@.svg'.format(app_id),
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps'
)

desktop_file_validate = find_program('desktop-file-validate', required: false)
if desktop_file_validate.found()
  test('validate-desktop', desktop_file_validate, args: [desktop_file])
endif

appstream_util = find_program('appstream-util', required: false)
if appstream_util.found()
  test('validate-metainfo', appstream_util, args: ['validate-strict', metainfo_file])
endif
