"""Theme handling for Gameyfin Desktop.

Two theme engines are supported side by side:

* **qt-material** — stylesheet based themes, named ``*.xml`` (e.g.
  ``dark_teal.xml``).
* **qt-themes** — QPalette based themes from
  https://github.com/beatreichenbach/qt-themes, named without an extension
  (e.g. ``nord``, ``catppuccin_mocha``).

Theme names are stored verbatim in ``GF_THEME``; the ``.xml`` suffix is what
tells the two engines apart, so the settings of existing installs keep working.
"""

import logging
import os

from PyQt6.QtGui import QPalette

_logger = logging.getLogger(__name__)

AUTO_THEME = "auto"

# Lazily filled by list_palette_themes(); loading the theme files touches disk.
_QT_THEMES_CACHE: dict | None = None


def _qt_themes():
    """Return the qt_themes module, or None when it is not installed."""
    # qt-themes talks to Qt through qtpy, which otherwise picks whichever
    # binding it finds first (PyQt5 on many systems) and would then set the
    # palette on a Qt that this app does not use.
    os.environ.setdefault("QT_API", "pyqt6")
    try:
        import qt_themes
    except ImportError:  # pragma: no cover - depends on the environment
        _logger.debug("qt-themes is not installed; palette themes unavailable.")
        return None
    return qt_themes


def list_material_themes() -> list[str]:
    """Return the available qt-material theme names (``*.xml``)."""
    try:
        from qt_material import list_themes
    except ImportError:  # pragma: no cover - depends on the environment
        return []
    try:
        return list(list_themes())
    except Exception:  # pragma: no cover - defensive
        _logger.warning("Could not list qt-material themes.", exc_info=True)
        return []


def list_palette_themes() -> list[str]:
    """Return the available qt-themes names (no extension)."""
    global _QT_THEMES_CACHE
    module = _qt_themes()
    if module is None:
        return []
    try:
        if _QT_THEMES_CACHE is None:
            _QT_THEMES_CACHE = module.get_themes()
        return sorted(_QT_THEMES_CACHE)
    except Exception:  # pragma: no cover - defensive
        _logger.warning("Could not list qt-themes themes.", exc_info=True)
        return []


def list_all_themes() -> list[str]:
    """Return every selectable theme name, qt-material first."""
    return list_material_themes() + list_palette_themes()


def is_palette_theme(theme: str | None) -> bool:
    """True when the theme is handled by qt-themes rather than qt-material."""
    if not theme or theme == AUTO_THEME:
        return False
    return not theme.endswith(".xml")


def is_light_theme(theme: str | None) -> bool:
    """Return True when the given theme is a light theme.

    qt-material encodes this in the name; qt-themes exposes it on the theme
    object, which is more reliable than guessing from names such as
    ``catppuccin_latte``.
    """
    if not theme or theme == AUTO_THEME:
        return False
    if is_palette_theme(theme):
        module = _qt_themes()
        if module is not None:
            try:
                theme_obj = module.get_theme(theme)
                if theme_obj is not None:
                    return not theme_obj.is_dark_theme()
            except Exception:  # pragma: no cover - defensive
                _logger.debug("Could not determine brightness of %r.", theme, exc_info=True)
    return "light" in theme.lower()


def _clear_material_env() -> None:
    """Drop the colours qt-material exports through the environment.

    They are how :func:`~gameyfin_frontend.utils.accent_color` reads a
    qt-material accent (that engine never touches the palette), so they must
    not outlive the theme that set them.
    """
    for key in [k for k in os.environ if k.startswith("QTMATERIAL_")]:
        del os.environ[key]


def _material_selection_override() -> str:
    """QSS that repaints qt-material's selected rows in a readable colour.

    qt-material hard-codes white text on its accent for selected list/menu
    items, which disappears on the light accents (amber, yellow, lime). The
    accent itself is fine — only the text on top of it has to follow the
    accent's brightness.
    """
    from PyQt6.QtGui import QColor

    from gameyfin_frontend.utils import contrasting_text_color

    rules = []
    for key, selectors in (
        ("QTMATERIAL_PRIMARYCOLOR",
         "QTableView::item:selected:focus, QTreeView::item:selected:focus, "
         "QListView::item:selected:focus"),
        ("QTMATERIAL_PRIMARYLIGHTCOLOR",
         "QComboBox::item:selected, QCalendarWidget QMenu::item:selected, "
         "QMenu::item:selected"),
    ):
        accent = QColor(os.environ.get(key, ""))
        if not accent.isValid():
            continue
        text = contrasting_text_color(accent).name()
        rules.append(f"{selectors} {{ color: {text}; selection-color: {text}; }}")
    return "\n" + "\n".join(rules) + "\n" if rules else ""


def _fix_highlighted_text(app) -> None:
    """Force selected text to a colour that stays readable on the accent.

    Several themes — palette themes and desktop themes alike — customise
    ``Highlight`` (a bright yellow, in the gruvbox-style ones) but leave
    ``HighlightedText`` near-white, which makes every selected row — combo box
    popups, list views, menus — unreadable. Derive it from the accent's
    brightness instead, the same way the hand-painted widgets do.
    """
    from gameyfin_frontend.utils import contrasting_text_color

    palette = app.palette()
    for group in (QPalette.ColorGroup.Active,
                  QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        highlight = palette.color(group, QPalette.ColorRole.Highlight)
        palette.setColor(group, QPalette.ColorRole.HighlightedText,
                         contrasting_text_color(highlight))
    app.setPalette(palette)


def reset_theme(app) -> None:
    """Restore the palette/font/style the application started with."""
    _clear_material_env()
    app.setStyleSheet("")
    if hasattr(app, "default_palette"):
        app.setPalette(app.default_palette)
    else:
        app.setPalette(QPalette())
    if hasattr(app, "default_font"):
        app.setFont(app.default_font)
    if hasattr(app, "default_style_name"):
        app.setStyle(app.default_style_name)


def apply_theme(app, theme: str | None) -> None:
    """Apply ``theme`` to ``app``, dispatching to the right theme engine."""
    if not theme or theme == AUTO_THEME:
        reset_theme(app)
        _fix_highlighted_text(app)
        return

    if is_palette_theme(theme):
        module = _qt_themes()
        if module is None:
            reset_theme(app)
            _fix_highlighted_text(app)
            return
        # qt-material leaves a stylesheet behind that would override the
        # palette, so start from a clean slate.
        _clear_material_env()
        app.setStyleSheet("")
        if hasattr(app, "default_font"):
            app.setFont(app.default_font)
        try:
            module.set_theme(theme)
        except Exception:  # pragma: no cover - defensive
            _logger.warning("Could not apply qt-themes theme %r.", theme, exc_info=True)
            reset_theme(app)
        _fix_highlighted_text(app)
        return

    try:
        from qt_material import apply_stylesheet
    except ImportError:  # pragma: no cover - depends on the environment
        _logger.warning("qt-material is not installed; cannot apply %r.", theme)
        reset_theme(app)
        _fix_highlighted_text(app)
        return
    # Drop any palette/style a previously applied qt-themes theme left behind;
    # qt-material only paints through a stylesheet and would inherit the rest.
    reset_theme(app)
    apply_stylesheet(app, theme=theme)
    app.setStyleSheet(app.styleSheet() + _material_selection_override())
