# build-aux/

## flatpak/

- `io.github.andrea_scape.ciak.json` — release Flatpak manifest (plain
  app-id, glycin sandbox enabled). Built by GitHub Actions on every tag.
- `ciak.gpg` — public GPG key for the flatpak repository. The CI workflow
  embeds it in `ciak.flatpakrepo` and signs the repo with the matching
  private key (stored as a GitHub Actions secret).
