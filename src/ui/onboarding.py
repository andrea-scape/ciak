import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

from ..domain.onboarding import OnboardingFlow, STEPS
from .. import config
from ..theme import apply_theme

STEP_TITLES = {
    "welcome": "Welcome to Ciak",
    "appearance": "Make it yours",
    "sync": "Stay in sync",
    "done": "You're ready",
}

THEME_VALUES = ["light", "dark", "default"]
THEME_LABELS = (("light", "Light"), ("dark", "Dark"), ("default", "Follow System"))


class OnboardingWindow(Adw.Window):
    """First-run wizard: TMDB key + appearance, shown before the main app."""

    def __init__(self, settings, on_finish, application=None):
        super().__init__(application=application)
        self.settings = settings
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
        self.skip_btn.add_css_class("dimmed")
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
        self._stack.add_named(self._page_appearance(), "appearance")
        self._stack.add_named(self._page_sync(), "sync")
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
                "Ciak tracks the movies and shows you watch. "
                "Pick a theme to get started. You can change it later "
                "in Preferences."
            )
        )
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

    def _page_sync(self):
        box, clamp = self._page_shell()
        box.append(self._heading(STEP_TITLES["sync"]))
        box.append(
            self._body(
                "Connect your favorite services to keep your watchlist, "
                "ratings, and history synced across all your devices.\n\n"
                "You can set these up in Settings after onboarding."
            )
        )

        prefs = Adw.PreferencesPage()
        prefs.set_vexpand(False)

        sync_group = Adw.PreferencesGroup()
        sync_group.set_title("Cloud Sync")
        prefs.add(sync_group)

        tmdb_row = Adw.ActionRow()
        tmdb_row.set_title("TMDB")
        tmdb_row.set_subtitle("Watchlist + ratings")
        sync_group.add(tmdb_row)

        simkl_row = Adw.ActionRow()
        simkl_row.set_title("Simkl")
        simkl_row.set_subtitle("Full sync — watchlist, watched, ratings, collection")
        sync_group.add(simkl_row)

        lb_row = Adw.ActionRow()
        lb_row.set_title("Letterboxd")
        lb_row.set_subtitle("Experimental — watchlist + ratings")
        sync_group.add(lb_row)

        box.append(prefs)

        skip = Gtk.Button(label="Skip for now")
        skip.add_css_class("flat")
        skip.add_css_class("dimmed")
        skip.set_halign(Gtk.Align.CENTER)
        skip.connect("clicked", lambda _b: self._go_forward())
        box.append(skip)

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
        self.flow.go_forward()
        self._sync_state()

    def _finish(self):
        self._finishing = True
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
