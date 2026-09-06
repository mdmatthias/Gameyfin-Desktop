import os

import pytest

from gameyfin_frontend import theming


class TestThemeListing:
    def test_lists_material_and_palette_themes(self):
        """Both theme engines must show up in the selection."""
        themes = theming.list_all_themes()
        assert "dark_teal.xml" in themes
        assert "nord" in themes

    def test_material_themes_come_first(self):
        """qt-material themes keep their original position at the top."""
        themes = theming.list_all_themes()
        assert themes[:len(theming.list_material_themes())] == theming.list_material_themes()


class TestThemeClassification:
    @pytest.mark.parametrize("theme", ["nord", "catppuccin_mocha", "github_light"])
    def test_palette_themes_detected(self, theme):
        assert theming.is_palette_theme(theme)

    @pytest.mark.parametrize("theme", ["dark_teal.xml", "light_blue.xml", "auto", "", None])
    def test_non_palette_themes(self, theme):
        assert not theming.is_palette_theme(theme)

    @pytest.mark.parametrize("theme,expected", [
        ("light_blue.xml", True),
        ("dark_teal.xml", False),
        ("github_light", True),
        ("github_dark", False),
        # Names give no hint here — the palette does.
        ("catppuccin_latte", True),
        ("catppuccin_mocha", False),
        ("auto", False),
        (None, False),
    ])
    def test_is_light_theme(self, theme, expected):
        assert theming.is_light_theme(theme) is expected


class TestApplyTheme:
    @pytest.fixture
    def app(self, qapp):
        qapp.default_palette = qapp.palette()
        qapp.default_font = qapp.font()
        qapp.default_style_name = qapp.style().objectName()
        yield qapp
        theming.reset_theme(qapp)

    def test_palette_theme_sets_palette_without_stylesheet(self, app):
        theming.apply_theme(app, "nord")
        assert app.styleSheet() == ""
        assert app.palette().window().color() != app.default_palette.window().color()

    def test_material_theme_repaints_selected_text_readably(self, app):
        """qt-material paints selections white, which vanishes on a light accent."""
        theming.apply_theme(app, "dark_amber.xml")

        css = app.styleSheet()
        assert css.rstrip().endswith("selection-color: #000000; }")
        assert "QListView::item:selected:focus { color: #000000;" in css
        assert "QMenu::item:selected { color: #000000;" in css

    def test_material_theme_keeps_white_on_a_dark_accent(self, app):
        theming.apply_theme(app, "dark_blue.xml")

        assert "color: #ffffff; selection-color: #ffffff; }" in app.styleSheet()

    def test_selected_text_contrasts_with_the_palette_accent(self, app):
        """A theme with a light accent but white selected text gets corrected."""
        from PyQt6.QtGui import QColor, QPalette

        palette = app.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#ffd740"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)
        app.default_palette = palette

        theming.apply_theme(app, "auto")

        assert app.palette().color(QPalette.ColorRole.HighlightedText).name() == "#000000"

    def test_material_theme_sets_stylesheet(self, app):
        theming.apply_theme(app, "dark_teal.xml")
        assert "QWidget" in app.styleSheet()

    def test_switching_from_palette_to_material_clears_palette(self, app):
        theming.apply_theme(app, "nord")
        theming.apply_theme(app, "dark_teal.xml")
        assert app.palette().window().color() == app.default_palette.window().color()

    def test_material_theme_exports_its_accent(self, app):
        from gameyfin_frontend.utils import accent_color

        theming.apply_theme(app, "dark_cyan.xml")

        assert os.environ["QTMATERIAL_PRIMARYCOLOR"] == "#4dd0e1"
        assert accent_color(app).name() == "#4dd0e1"

    def test_leaving_a_material_theme_drops_its_accent(self, app):
        theming.apply_theme(app, "dark_cyan.xml")

        theming.apply_theme(app, "auto")

        # Left behind, the exported accent would outlive its theme and keep
        # colouring selections after the user switched away from it.
        assert "QTMATERIAL_PRIMARYCOLOR" not in os.environ

    def test_auto_restores_defaults(self, app):
        theming.apply_theme(app, "nord")
        theming.apply_theme(app, "auto")
        assert app.styleSheet() == ""
        assert app.palette().window().color() == app.default_palette.window().color()
