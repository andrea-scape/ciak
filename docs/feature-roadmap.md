# Ciak Feature Roadmap

> **For agentic workers:** Each feature below is a standalone implementation plan. Work through them in the listed order. Each feature starts with a description of what it means and what the agent should build, followed by the files it touches and key implementation notes.

---

## Feature 1: Export Data (CSV / JSON) -- DONE

**What it does:** Lets users export their entire library (watchlist, watched history, ratings, collection) as CSV or JSON files. The formats should match Trakt and Letterboxd export schemas so users can import their data into those services.

**Why it matters:** Data portability. Users won't commit to a tracker they can't leave. Trakt's CSV format has columns like `Title`, `Year`, `IMDB ID`, `Rating`, `Watched Date`. Letterboxd's CSV export has `Name`, `Year`, `Letterboxd URI`, `Rating`, `Rewatch`, `Tags`, `Watched Date`. Matching these formats means Ciak users can switch to Trakt/Letterboxd without losing anything.

**Files to create/modify:**
- Create: `src/data/export.py` — export logic, CSV/JSON writers, format mappers
- Modify: `src/ui/preferences_page.py` — add "Export" button in Advanced tab
- Modify: `src/data/local/repository.py` — add methods to fetch full user data with timestamps for export

**Key details:**
- CSV export for Trakt format: `Title,Year,IMDB ID,Type,Rating,Watched Date,Stopped Date`
- CSV export for Letterboxd format: `Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags`
- JSON export: full structured dump of all user data (watchlist, watched, ratings, collection)
- Use `Gtk.FileDialog` to let users choose save location and format
- Export must run in a background thread (use `src/threads.py`)
- The watched_items table already stores `watched_at` as a Unix timestamp — convert to ISO date for CSV

**Schema note:** The `watched_items` table already has `watched_at INTEGER` (Unix timestamp). The `ratings` table has `rated_at INTEGER`. The `watchlist_items` table has `added_at INTEGER`. All needed data is already stored.

---

## Feature 2: Import Data (CSV / JSON) -- DONE

**What it does:** Lets users import watch history, ratings, and watchlists from Trakt CSV, Letterboxd CSV, IMDb CSV, or generic JSON into Ciak. Shows a preview of matched items before committing, so users can review and exclude wrong matches.

**Why it matters:** Users switching from Trakt, Letterboxd, or IMDb need to bring their history with them. FlickPicker built its reputation on reliable imports. The preview step matters because older films and shows with ambiguous names often match incorrectly.

**Files to create/modify:**
- Create: `src/data/importers.py` — parser classes for each format (TraktCSV, LetterboxdCSV, IMDbCSV, GenericJSON)
- Create: `src/ui/import_dialog.py` — preview dialog showing matched/unmatched items before import
- Modify: `src/ui/preferences_page.py` — add "Import" button in Advanced tab
- Modify: `src/data/local/repository.py` — bulk insert methods for imported items

**Key details:**
- Trakt CSV columns: `Title,Year,IMDB ID,Type,Rating,Watched Date,Stopped Date`
- Letterboxd CSV columns: `Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags`
- IMDb CSV columns: `Position,Const,Type,Title,Original Title,TV Series,Year,Runtime (mins),Genres,Rating,Votes,Resume,URL`
- Each parser normalizes to a common `ImportItem` dataclass: `title, year, imdb_id, media_type, rating, watched_date, tags`
- Match imported items to Ciak's DB by TMDB ID (if available) or by title+year fuzzy match
- Use TMDB API to resolve IMDB IDs to TMDB IDs where needed (`/find/{imdb_id}?external_source=imdb_id`)
- The import dialog shows: matched items (green), unmatched items (red), duplicate items (yellow)
- Bulk insert with a single transaction for performance
- Run TMDB lookups in parallel using `src/threads.py`

---

## Feature 3: Watch Date Logging -- DONE

**What it does:** Records and displays the date when each item was watched. Currently `watched_at` is stored as a Unix timestamp but never shown to the user. This feature surfaces that data in the history page and detail page.

**Why it matters:** Knowing *when* you watched something is the foundation for diary views, streaks, and the upcoming history page rework. Letterboxd's diary is its most loved feature, and it's built entirely on watch dates.

**Files to create/modify:**
- Modify: `src/data/local/repository.py` — `get_watched_list()` already returns `watched_at`; ensure it's passed through
- Modify: `src/ui/history_page.py` — show watch date on each card
- Modify: `src/ui/detail_page.py` — show "Watched on <date>" below the Watched button
- Modify: `src/ui/media_card.py` — add optional date subtitle to card widget

**Key details:**
- `watched_items.watched_at` is already a Unix timestamp (INTEGER). Convert to `datetime.date` for display.
- Format dates as "Aug 15, 2026" (locale-aware via `GLib.date_time_format`)
- On the detail page, when an item is marked watched, show the date beneath the checkmark
- On the history page, each card should show the watch date as a secondary label
- The watchlist_items table also has `added_at` — show "Added Aug 10, 2026" on watchlist cards
- This is a small feature. No schema changes needed — the data is already there.

---

## Feature 4: Streaming Availability -- DONE

**What it does:** Shows where a movie or TV show is available to stream, rent, or buy in the user's region. Uses the TMDB Watch Providers API endpoint.

**Why it matters:** JustWatch exists entirely for this one job. Showing streaming availability on the detail page saves users from leaving the app to check if something is on Netflix, Prime, or Apple TV.

**Files to create/modify:**
- Modify: `src/data/tmdb/client.py` — add `get_watch_providers(tmdb_id, media_type)` method
- Modify: `src/data/tmdb/service.py` — add `get_streaming_info()` to MetadataService
- Modify: `src/domain/models.py` — add `StreamingInfo` dataclass, add `streaming` field to Movie/Show
- Modify: `src/ui/detail_page.py` — add streaming providers section below the hero
- Modify: `src/style.css` — style for provider logos row

**Key details:**
- TMDB endpoint: `GET /movie/{id}/watch/providers` or `/tv/{id}/watch/providers`
- Response contains `results` keyed by country code (e.g. `"US"`, `"IT"`). Each entry has `flatrate` (streaming), `rent`, `buy` arrays with `provider_name` and `provider_id` (for logo).
- Provider logos: `https://image.tmdb.org/t/p/original/{logo_path}`
- Store user's country in GSettings (default to system locale, configurable in Preferences)
- Show provider logos as a horizontal row of small circular images (like Letterboxd does)
- Group by type: "Stream" (flatrate), "Rent", "Buy" — each with its own label
- Cache the provider data alongside other metadata (add to `media_items` as a JSON column, or cache separately)

**Schema note:** Add `watch_providers TEXT` (JSON) column to `media_items` in cache.py. The cache migration pattern is already established.

---

## Feature 5: Custom Lists

**What it does:** Lets users create named custom lists (e.g. "Date Night", "Comfort Rewatches", "Oscar Catch-up") and add movies/shows to them. Items can belong to multiple lists.

**Why it matters:** The watchlist is a single flat list. Custom lists let users organize their watchlist by mood, occasion, or theme. Letterboxd's entire social layer is built on lists. Trakt supports custom lists too.

**Files to create/modify:**
- Modify: `src/data/local/schema.py` — add `custom_lists` and `custom_list_items` tables
- Modify: `src/data/local/repository.py` — CRUD for lists and list items
- Modify: `src/domain/protocols.py` — extend UserMediaRepository with list methods
- Create: `src/ui/lists_page.py` — new sidebar page showing all custom lists
- Modify: `src/ui/main_page.py` — add "Lists" to sidebar navigation
- Modify: `src/ui/detail_page.py` — add "Add to List" button that opens a list picker
- Modify: `src/style.css` — styles for list cards

**Key details:**
- New table `custom_lists`: `id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, created_at INTEGER`
- New table `custom_list_items`: `list_id INTEGER, tmdb_id INTEGER, media_type TEXT, added_at INTEGER, PRIMARY KEY (list_id, tmdb_id, media_type)`
- The sidebar gets a "Lists" section below the main pages, showing each list as a row
- Clicking a list opens a grid view of its items (reuse `make_media_card` from media_card.py)
- The detail page gets an "Add to List" button next to Watchlist/Watched/Rate
- Tapping "Add to List" opens a popover with checkboxes for each list
- Lists are sorted alphabetically by default
- The profile page shows list counts in the stats section

---

## Feature 6: Diary View (History Page Rework) -- DONE

**What it does:** Reworks the history page from a flat grid into a timeline view grouped by date, similar to Letterboxd's diary. Each day shows the items watched on that date with ratings and optional notes.

**Why it matters:** The current history page is just a grid of watched items — identical to the watchlist layout. A diary view turns watching history into a personal journal. Letterboxd users cite the diary as their favorite feature.

**Files to create/modify:**
- Rewrite: `src/ui/history_page.py` — replace grid with date-grouped timeline
- Modify: `src/style.css` — styles for diary date headers and entry rows
- Modify: `src/data/local/repository.py` — `get_watched_list()` already returns `watched_at`; add a method that groups by date

**Key details:**
- Group watched items by `watched_at` date, newest first
- Each date group has a header ("Thursday, August 15, 2026") and a horizontal scrollable row of cards
- Each card shows: poster, title, rating (if rated), and a small star count
- Use `Gtk.ListView` or `Gtk.Box` with vertical orientation for the date groups
- Each date group uses a horizontal `Gtk.Box` or `Gtk.FlowBox` for the cards
- Add a "Notes" field to diary entries (optional text per watch session) — this requires a schema change
- The diary view replaces the current history grid entirely — toggle between "Grid" and "Diary" views in the page header
- Sort by date descending (most recent first)
- Show total watch count and total time per day in the date header

**Schema note:** Add `notes TEXT` column to `watched_items` for optional per-watch-session notes.

---

## Feature 7: Episode Notifications

**What it does:** Sends desktop notifications when new episodes of tracked shows air. Uses the calendar data already fetched by the calendar page.

**Why it matters:** Users add shows to their watchlist to stay current. Without notifications, they have to remember to check the calendar page. Desktop notifications bridge that gap.

**Files to create/modify:**
- Create: `src/notifications.py` — notification logic, scheduling
- Modify: `src/ui/preferences_page.py` — add "Notifications" toggle in a new or existing tab
- Modify: `src/main.py` — start notification scheduler on app launch
- Modify: `src/data/tmdb/service.py` — expose next-air-date data for tracked shows

**Key details:**
- Use `Gio.Notification` (GNOME's standard notification API) — works in Flatpak sandbox
- Check every 6 hours (or on app launch) for shows with episodes airing today
- The `media_items` table already stores `next_episode_air_date`, `next_episode_name`, etc.
- Query: `SELECT * FROM media_items WHERE media_type='show' AND next_episode_air_date = date('now')`
- Notification body: "<show_name> — S<season>E<episode> airs today: <episode_name>"
- Add a GSettings key `notifications-enabled` (default True) for the preference toggle
- On Flatpak, notifications require the `org.freedesktop.portal.Notification` permission — already covered by session-bus access in finish-args
- Notification action: clicking it opens the detail page for that show

**Schema note:** No schema changes needed. The `next_episode_*` columns already exist in `media_items`.

---

## Feature 8: Collections / Sagas -- DONE

**What it does:** Groups movies into collections (e.g. "Marvel Cinematic Universe", "Lord of the Rings trilogy", "Star Wars saga"). Shows collection membership on the detail page and lets users browse entire collections.

**Why it matters:** TMDB already provides collection data — Ciak has `collection_id` in the database but never uses it. Collections help users understand what they've watched within a franchise and what's missing.

**Files to create/modify:**
- Modify: `src/data/tmdb/client.py` — `get_collection()` already exists; ensure it returns full parts list
- Modify: `src/data/tmdb/service.py` — add `get_collection_parts()` method
- Modify: `src/ui/detail_page.py` — add "Part of: <collection_name>" link below the title
- Create: `src/ui/collection_page.py` — grid view of all movies in a collection
- Modify: `src/ui/main_page.py` — add `show_collection()` navigation method
- Modify: `src/style.css` — collection page styles

**Key details:**
- TMDB endpoint: `GET /collection/{id}` returns `name`, `overview`, `parts` (list of movies)
- On the detail page, if `movie.collection_id` is set, show a clickable chip: "Part of: Marvel Cinematic Universe"
- Tapping the chip opens a new page showing all movies in that collection as a grid
- Mark which movies in the collection the user has watched (green checkmark overlay)
- The collection page shows: collection name, overview, total movies, movies watched, movies remaining
- Cache collection data alongside other metadata
- Collections are movie-only — TMDB doesn't have show collections (use "franchise" or "network" if needed later)

**Schema note:** The `collection_items` table already exists in schema.py (line 121-130) with `tmdb_id`, `media_type`, `collected_at`. This is for user-owned items (physical media). The TMDB collection data is separate — it's a metadata concept, not a user collection.

---

## Feature 9: Keyboard Shortcut Customization

**What it does:** Lets users customize all keyboard shortcuts through the Preferences page. Currently some shortcuts are hardcoded and toggleable but not rebindable.

**Why it matters:** Power users expect to remap shortcuts to match their workflow. GNOME apps increasingly offer shortcut customization via `Adw.ShortcutsDialog`.

**Files to create/modify:**
- Modify: `src/ui/preferences_page.py` — add "Shortcuts" tab with shortcut editor
- Modify: `src/ui/main_page.py` — load shortcuts from GSettings instead of hardcoded values
- Modify: `src/window.py` — register shortcuts from GSettings on startup

**Key details:**
- Use `Adw.ShortcutsGroup` and `Adw.ShortcutsSection` in the Preferences dialog for display
- For actual editing, use a custom row widget: label showing current shortcut + a button to record a new one
- Store shortcuts in GSettings as a JSON string under a new key `custom-shortcuts`
- Default shortcuts (hardcoded fallback): `<Ctrl>f` search, `<Ctrl>1-5` pages, `<Ctrl>q` quit, `<Escape` close detail
- The shortcut recorder captures `<Ctrl><Shift>a` style key combos using `Gtk.EventControllerKey`
- Validate: no duplicate shortcuts, no reserved system combos (Alt+Tab, etc.)
- A "Reset to defaults" button in the shortcuts tab
- Each shortcut row shows: action name, current keybinding, edit button, reset button

---

## Feature 10: Notifications for New Episodes (Expanded)

**What it does:** Expansion of Feature 7. Adds per-show notification toggles, a "Notify me" toggle on each show's detail page, and notification history.

**Why it matters:** Not every tracked show warrants a notification. Users want to cherry-pick which shows ping them — the finale of a beloved series, yes; a background show they watch casually, no.

**Files to create/modify:**
- Modify: `src/data/local/schema.py` — add `notify INTEGER DEFAULT 1` column to `media_items`
- Modify: `src/ui/detail_page.py` — add "Notify" toggle switch on show detail pages
- Modify: `src/notifications.py` (from Feature 7) — filter by `notify=1` when checking for today's episodes
- Modify: `src/ui/preferences_page.py` — add per-show notification settings

**Key details:**
- Add `notify INTEGER DEFAULT 1` column to `media_items` — all existing shows default to notified
- On the detail page for shows, add a bell icon toggle next to the other action buttons
- Toggling the bell updates `media_items.notify` for that show
- The notification scheduler (from Feature 7) only fires for shows where `notify=1`
- In Preferences, add a "Notify for all shows" master toggle that overrides per-show settings
- Store notification history in a new table `notification_history`: `id, show_tmdb_id, season_number, episode_number, notified_at`
- Show notification history in Preferences so users can see what they were notified about
- Deduplicate: don't notify twice for the same episode (check `notification_history` before sending)

**Schema note:** Add `notify INTEGER DEFAULT 1` to `media_items` via cache migration. Add `notification_history` table via schema migration (new version 3).
