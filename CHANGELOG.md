# Changelog

## 0.1.8 - 2026-09-19

- The calendar "Upcoming" popover opens reliably: it lists only the releases airing from today onward in the shown month, scrolls when long, adjusts to the window, and its count always matches the releases listed
- Diary history can be filtered by genre chips drawn from your watched titles
- Diary month dividers render fully with a floating "month in view" chip, and scrolling no longer re-lays out the page
- Poster reveal animation animates opacity only, removing the per-frame layout that stuttered dense grids
- Watchlist, search and collection cards are pinned to their poster width, keeping every grid column even
- A "Send Test Notification" button and BETA badge appear in the airing-notification preferences; enabling airing notifications checks for due episodes right away

## 0.1.7 - 2026-09-11

- Watchlist grid appears instantly; hiding of caught-up and unreleased titles now happens after the first paint
- Detail pages show the title, year, and plot right away from local data
- Search reveals its first results without waiting for posters
- Unreleased-title checks run in parallel, and network requests fail faster
- Posters reveal with their info captions once painted, and missing posters are prefetched
- BETA indicator on development builds

## 0.1.5 - 2026-08-16

- Marking a title as watched opens the rating dialog right away (skipped when already rated)
- Movie details show the precise release date instead of just the year
- Upcoming episodes show their airing date on the details page
- Movies and shows that have not been released yet cannot be marked as watched or rated
- Development builds are named "Ciak (Development)" in the app grid

## 0.1.4 - 2026-08-16

- Responsive detail page: hero buttons wrap, the poster stacks on top on narrow windows, and the sidebar collapses to an overlay with icon-only filters under 900px
- Posters keep their fixed 2:3 shape when the detail hero stacks
- Details page shows budget and revenue, and friendlier runtimes
- Director and showrunners are listed at the top of the cast
- Rating a title also marks it as watched
- Minimum window size reduced to 750x750

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
