# Contributing

## Development setup

Build the development Flatpak (app-id `io.github.andrea_scape.ciak.Devel`):

```bash
flatpak run org.flatpak.Builder --user --install \
  build-aux/flatpak/build \
  io.github.andrea_scape.ciak.Devel.json
flatpak run io.github.andrea_scape.ciak.Devel
```

The dev build shows a "Development" pill at the bottom of the sidebar, so you
can tell it apart from a release install.

See the README for full build instructions, dependency requirements, and
the project layout.

## Pull requests

- Keep changes small, one thing per PR
- Run the test suite before opening (`flatpak run --command=python3 io.github.andrea_scape.ciak.Devel -m unittest discover -s tests`)
- If you touch a Flatpak manifest, verify the bundle builds
