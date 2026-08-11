# Changelog

## 0.1.3 - 2026-08-11

- Development builds show a "Development" pill (terminal icon + label) at the
  bottom of the sidebar, so a dev build is recognizable at a glance
- Dev-build detection now reads the runtime FLATPAK_ID instead of the compiled
  app-id, so the pill follows the manifest you actually run (GNOME Builder and
  installed flatpaks alike)
- Rating dialog no longer closes when clicking a star to rate (inverted
  hit-test on click-away)
- The details-page Rate button shows the current rating ("Rated ★ 4/5") with an
  active style once a movie or series has been rated
- Rating dialog hover preview no longer throws when the pointer leaves a star
  (handler signature matched to the motion controller's leave signal)
- The Rate button keeps showing "Rated ★ 4/5" after the app restarts — the
  saved rating is loaded again when the details page opens
- Details-page action buttons keep a fixed width, so toggling Watchlist /
  Watched / Rate no longer reshuffles the layout
- Removed the unused detail-page fetch path

## 0.1.2 - 2026-08-08

- Symbolic icons render in sandboxed environments (icon-theme fallback to Adwaita)
- External links open through the desktop portal (session bus added to finish-args)
- Release builds: plain app-id with glycin sandbox enabled; CI publishes a personal flatpak repository on GitHub Pages
- Meson best practices: post-install hooks, metadata validation, i18n, test suite wiring
- Poster loading crash fixed (GLib import in texture loader)
- `desktop-file-name-suffix` removed; Keywords and Categories added to the desktop file
- AppStream: `<icon type="cached">` and `<developer_name>` added
- Icon theme helper module (`src/icon_theme.py`) with tests

## 0.1.1 - 2026-08-06

- First-run onboarding wizard: theme picker, adult content toggle, TMDB API key validation
- Relaunch onboarding from Preferences → Advanced
- Donation link wired to Buy Me a Coffee
- Removed "Trakt client" wording; Trakt sync is coming in a future update
- Developer name now reads Andrea Scaperrotta
- AppStream metadata: screenshots, keywords, categories, icon, bugtracker/donation/help URLs
- Desktop file categories fixed (removed Player)

## 0.1.0 - 2026-07-29

First release.

- Watchlist, search, detail, calendar, history and profile pages
- Ratings (1-5 stars) with a picker dialog
- Per-season episode tracking with upcoming air dates
- Local SQLite storage, poster caching
- Preferences: TMDB API key, theme, keyboard shortcuts, sidebar behavior
- GNOME 50 runtime, Flatpak bundle attached to GitHub Releases
