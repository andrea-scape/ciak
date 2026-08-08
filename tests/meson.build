python = import('python')
python_bin = python.find_installation('python3')

test_env = environment()
test_env.set('PYTHONPATH', meson.project_source_root() / 'src')

test('unit-tests', python_bin,
  args: ['-m', 'unittest', 'discover', '-s', 'tests'],
  env: test_env,
)
