import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

import threading

from ..domain.onboarding import OnboardingFlow, STEPS
from .. import config
from ..theme import apply_theme

STEP_TITLES = {
    "welcome": "Welcome to Ciak",
    "tmdb": "Your TMDB key",
    "appearance": "Make it yours",
    "done": "You're ready",
}

THEME_VALUES = ["light", "dark", "default"]
THEME_LABELS = (("light", "Light"), ("dark", "Dark"), ("default", "Follow System"))

STATUS_COPY = {
    "valid": "That key works.",
    "invalid": "TMDB didn't accept that key. Copy it again from your TMDB account.",
    "unreachable": (
        "Couldn't reach TMDB. Check your connection and try again, "
        "or continue and finish setup later."
    ),
}


class OnboardingWindow(Adw.Window):
    """First-run wizard: TMDB key + appearance, shown before the main app."""

    def __init__(self, settings, tmdb_client, on_finish, application=None):
        super().__init__(application=application)
        self.settings = settings
        self.tmdb_client = tmdb_client
        self._on_finish = on_finish
        self._finishing = False
        self.flow = OnboardingFlow()

        self.set_title("Ciak")
        self.set_default_size(760, 640)
        self.set_size_request(620, 560)

        toolbar = Adw.ToolbarView()
        header = self._build_header()
        toolbar.add_top_bar(header)
        toolbar.add_bottom_bar(self._build_actionbar())
        toolbar.set_content(self._build_stack())
        self.set_content(toolbar)

        self.connect("close-request", self._on_close_request)
        self._sync_state()

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------

    def _build_header(self):
        self._header = Adw.HeaderBar()
        self._header.set_show_title(False)
        self._header_title = Gtk.Label(label="")
        self._header_title.add_css_class("title")
        self._header.set_title_widget(self._header_title)

        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_btn.set_tooltip_text("Back")
        self.back_btn.connect("clicked", lambda _b: self._go_back())
        self._header.pack_start(self.back_btn)
        return self._header

    def _build_actionbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.set_margin_top(6)
        bar.set_margin_bottom(20)
        bar.set_margin_start(12)
        bar.set_margin_end(12)

        self.skip_btn = Gtk.Button(label="Skip setup")
        self.skip_btn.add_css_class("flat")
        self.skip_btn.add_css_class("dim-label")
        self.skip_btn.set_tooltip_text("Skip setup and open Ciak")
        self.skip_btn.connect("clicked", lambda _b: self._finish())
        bar.append(self.skip_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)

        self.next_btn = Gtk.Button(label="Continue")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", lambda _b: self._go_forward())
        bar.append(self.next_btn)
        return bar

    def _build_stack(self):
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(200)
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)

        self._stack.add_named(self._page_welcome(), "welcome")
        self._stack.add_named(self._page_tmdb(), "tmdb")
        self._stack.add_named(self._page_appearance(), "appearance")
        self._stack.add_named(self._page_done(), "done")
        return self._stack

    @staticmethod
    def _page_shell():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_hexpand(True)
        clamp = Adw.Clamp(maximum_size=520)
        clamp.set_child(box)
        return box, clamp

    @staticmethod
    def _heading(text):
        label = Gtk.Label(label=text)
        label.add_css_class("title-1")
        label.set_halign(Gtk.Align.CENTER)
        label.set_wrap(True)
        return label

    @staticmethod
    def _body(text):
        label = Gtk.Label(label=text)
        label.add_css_class("body")
        label.set_halign(Gtk.Align.CENTER)
        label.set_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        return label

    @staticmethod
    def _app_icon():
        icon = Gtk.Image(icon_name=config.APP_ID)
        icon.set_pixel_size(96)
        icon.set_halign(Gtk.Align.CENTER)
        icon.add_css_class("about-icon")
        return icon

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def _page_welcome(self):
        box, clamp = self._page_shell()
        box.append(self._app_icon())
        box.append(self._heading(STEP_TITLES["welcome"]))
        box.append(
            self._body(
                "Ciak tracks the movies and shows you watch. Two quick steps: "
                "add a TMDB key and pick a theme. You can change both later "
                "in Preferences."
            )
        )
        return clamp

    def _page_tmdb(self):
        box, clamp = self._page_shell()
        box.append(self._heading(STEP_TITLES["tmdb"]))
        box.append(
            self._body(
                "Ciak pulls titles and posters from The Movie Database. "
                "Get a free key and paste it below."
            )
        )

        self.key_entry = Gtk.Entry()
        self.key_entry.set_text(self.settings.get_string("tmdb-api-key"))
        self.key_entry.set_hexpand(True)
        self.key_entry.set_activates_default(True)
        self.key_entry.connect("changed", self._on_key_changed)
        box.append(self.key_entry)

        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("body")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_wrap(True)
        self.status_label.set_visible(False)
        box.append(self.status_label)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        action_row.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        action_row.append(self.spinner)

        self.test_btn = Gtk.Button(label="Test connection")
        self.test_btn.connect("clicked", lambda _b: self._start_validate(False))
        action_row.append(self.test_btn)
        box.append(action_row)

        link = Gtk.LinkButton.new_with_label(
            "https://www.themoviedb.org/settings/api",
            "Get a key at themoviedb.org",
        )
        link.set_halign(Gtk.Align.CENTER)
        box.append(link)

        skip = Gtk.Button(label="Skip for now")
        skip.add_css_class("flat")
        skip.add_css_class("dim-label")
        skip.set_halign(Gtk.Align.CENTER)
        skip.connect("clicked", lambda _b: self._on_skip())
        box.append(skip)
        return clamp

    def _page_appearance(self):
        box, clamp = self._page_shell()
        box.append(self._heading(STEP_TITLES["appearance"]))

        prefs = Adw.PreferencesPage()
        prefs.set_vexpand(False)

        appearance = Adw.PreferencesGroup()
        appearance.set_title("Appearance")
        prefs.add(appearance)

        self.theme_row = Adw.ComboRow()
        self.theme_row.set_title("Theme")
        self.theme_row.set_subtitle("Force light or dark, or follow the system")
        theme_model = Gtk.StringList()
        for _value, label in THEME_LABELS:
            theme_model.append(label)
        self.theme_row.set_model(theme_model)
        current = self.settings.get_string("theme")
        self.theme_row.set_selected(
            THEME_VALUES.index(current) if current in THEME_VALUES else 2
        )
        self.theme_row.connect("notify::selected", self._on_theme_changed)
        appearance.add(self.theme_row)

        content = Adw.PreferencesGroup()
        content.set_title("Content")
        prefs.add(content)

        adult_row = Adw.SwitchRow()
        adult_row.set_title("Hide Adult Content")
        adult_row.set_subtitle("Exclude adult content from search results")
        self.settings.bind(
            "hide-adult-content",
            adult_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        content.add(adult_row)

        box.append(prefs)
        return clamp

    def _page_done(self):
        box, clamp = self._page_shell()
        box.append(self._app_icon())
        box.append(self._heading(STEP_TITLES["done"]))
        box.append(
            self._body(
                "Ciak is set up. Add a movie to your watchlist and see "
                "what's next."
            )
        )
        return clamp

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_key_changed(self, entry):
        self.flow.set_key(entry.get_text().strip())
        self.flow.set_status(None)
        self.status_label.set_visible(False)
        self.status_label.remove_css_class("error")
        self._sync_state()

    def _start_validate(self, advance_on_success):
        key = self.key_entry.get_text().strip()
        self.flow.set_key(key)
        if not key:
            self.flow.set_status(None)
            self._sync_state()
            return
        self.status_label.set_visible(False)
        self.test_btn.set_sensitive(False)
        self.next_btn.set_sensitive(False)
        self.spinner.set_visible(True)
        self.spinner.start()
        threading.Thread(
            target=self._validate_worker, args=(key, advance_on_success), daemon=True
        ).start()

    def _validate_worker(self, key, advance_on_success):
        status = self.tmdb_client.validate_key()
        GLib.idle_add(self._apply_validate_result, status, advance_on_success)

    def _apply_validate_result(self, status, advance_on_success):
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.test_btn.set_sensitive(True)
        self.next_btn.set_sensitive(True)
        if status == "valid":
            self._persist_key()
        self.flow.set_status(status)
        self._show_status(status)
        if advance_on_success and status != "invalid":
            self.flow.go_forward()
        self._sync_state()

    def _persist_key(self):
        key = self.key_entry.get_text().strip()
        if key:
            self.settings.set_string("tmdb-api-key", key)

    def _show_status(self, status):
        if not status:
            self.status_label.set_visible(False)
            return
        self.status_label.set_text(STATUS_COPY[status])
        self.status_label.remove_css_class("error")
        if status == "invalid":
            self.status_label.add_css_class("error")
        self.status_label.set_visible(True)

    def _on_skip(self):
        self.flow.skip_tmdb()
        self.status_label.set_visible(False)
        self.flow.go_forward()
        self._sync_state()

    def _on_theme_changed(self, row, _gparam):
        value = THEME_VALUES[row.get_selected()]
        self.settings.set_string("theme", value)
        apply_theme(self.settings)

    def _go_back(self):
        self.flow.go_back()
        self._sync_state()

    def _go_forward(self):
        if self.flow.step == "done":
            self._finish()
            return
        if self.flow.step == "tmdb":
            key = self.key_entry.get_text().strip()
            self.flow.set_key(key)
            if key and self.flow.tmdb_status is None:
                self._start_validate(True)
                return
            if self.flow.tmdb_status == "invalid":
                return
        self.flow.go_forward()
        self._sync_state()

    def _finish(self):
        self._finishing = True
        self._persist_key()
        self.settings.set_boolean("onboarding-completed", True)
        apply_theme(self.settings)
        self._on_finish()

    def _on_close_request(self, *_args):
        if not self._finishing:
            app = self.get_application()
            if app is not None:
                app.quit()
        return False

    # ------------------------------------------------------------------
    # State sync
    # ------------------------------------------------------------------

    def _sync_state(self):
        step = self.flow.step
        self._stack.set_visible_child_name(step)
        index = STEPS.index(step) + 1
        self._header_title.set_text(f"Step {index} of {len(STEPS)}")
        self.back_btn.set_sensitive(self.flow.can_go_back())
        if step == "done":
            self.next_btn.set_label("Let's go")
            self.next_btn.set_sensitive(True)
        else:
            self.next_btn.set_label("Continue")
            self.next_btn.set_sensitive(self.flow.can_go_forward())
