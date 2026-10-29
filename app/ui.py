"""Presentation helpers for templates: the inline SVG icon set.

All icons share one style: 24×24 grid, 1.75 px round strokes in the current
text colour, no fill. Drinks use one of five icons by type (stored as
Drink.icon); unknown values fall back to the espresso cup.
"""

from markupsafe import Markup, escape

from app.services import clock

# name -> SVG child elements (drawn on a 24×24 grid)
ICON_PATHS = {
    # Drink types
    "espresso": '<path d="M5 9h11v4a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z"/>'
                '<path d="M16 10h1.5a2.5 2.5 0 0 1 0 5H16"/><path d="M3 21h16"/>'
                '<path d="M9 3.5c0 1.5 1.5 1.5 1.5 3M12.5 3.5c0 1.5 1.5 1.5 1.5 3"/>',
    "milk": '<path d="M7 3h10l-1.6 18H8.6z"/><path d="M7.6 9h8.8"/><path d="M8.1 14h7.8"/>',
    "mug": '<path d="M5 7h11v9a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"/>'
           '<path d="M16 10h1.5a2.5 2.5 0 0 1 0 5H16"/><path d="M5 11h11"/>',
    "tea": '<path d="M4 10h13v1.5a6.5 6.5 0 0 1-6.5 6.5A6.5 6.5 0 0 1 4 11.5z"/>'
           '<path d="M17 11h1a2.5 2.5 0 0 1 0 5h-1.6"/><path d="M3 21h15"/>'
           '<path d="M10.5 10V6.5"/><rect x="9" y="3.5" width="3" height="3" rx=".5"/>',
    "iced": '<path d="M6 5h12l-1.6 16H7.6z"/><path d="M13.5 5l2.5-3"/>'
            '<rect x="8.5" y="9" width="3.2" height="3.2" rx=".6"/>'
            '<rect x="12.3" y="12.5" width="3.2" height="3.2" rx=".6"/>',
    # Interface
    "star": '<path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6l-5.4 2.9 1.2-6-4.5-4.2 6.1-.7z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "user-plus": '<circle cx="10" cy="8" r="4"/><path d="M2.5 21a7.5 7.5 0 0 1 15 0"/>'
                 '<path d="M19 7v6M16 10h6"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M14.5 8.5l2 2"/>',
    "wrench": '<path d="M14.6 6.4a4 4 0 0 0-5.3 5.3l-5.8 5.8 3 3 5.8-5.8a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.4-.6-.6-2.4z"/>',
    "sliders": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
               '<circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "card": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>',
    "contactless": '<path d="M7.5 8.5a5 5 0 0 1 0 7M11 6a8.5 8.5 0 0 1 0 12M14.5 3.5a12.5 12.5 0 0 1 0 17"/>',
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
    "id-card": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2"/>'
               '<path d="M5.8 16a3.2 3.2 0 0 1 6.4 0M14 10h4M14 14h3"/>',
    "receipt": '<path d="M6 3h12v18l-2-1.5-2 1.5-2-1.5-2 1.5-2-1.5L6 21z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 7l8.5 6 8.5-6"/>',
    "alert": '<path d="M12 3.5L21.5 20h-19z"/><path d="M12 10v4.5M12 17.3v.2"/>',
    "stop": '<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8z"/><path d="M8.5 12h7"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
    "timer": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M10 2.5h4"/>',
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/>',
    "logout": '<path d="M15 4h4v16h-4"/><path d="M10 8l-4 4 4 4M6 12h10"/>',
    "online": '<path d="M2 9.5a15 15 0 0 1 20 0M5 13a10 10 0 0 1 14 0M8.5 16.5a5 5 0 0 1 7 0"/>'
              '<path d="M12 20h.01"/>',
    "offline": '<path d="M3 3l18 18"/><path d="M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 4.7-2.6'
               'M14.6 10.6A10 10 0 0 1 19 13M2 9.5a15 15 0 0 1 4.3-2.7M10.5 5.1A15 15 0 0 1 22 9.5"/>'
               '<path d="M12 20h.01"/>',
    "bin": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/><path d="M10 11v5M14 11v5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4'
           'M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "cloudy": '<path d="M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9.5a4.3 4.3 0 0 1-.5 8.5z"/>',
    "rainy": '<path d="M7 15a4 4 0 0 1-.5-8A6 6 0 0 1 18 6.5a4.3 4.3 0 0 1-.5 8.5z"/>'
             '<path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
    "snowy": '<path d="M7 15a4 4 0 0 1-.5-8A6 6 0 0 1 18 6.5a4.3 4.3 0 0 1-.5 8.5z"/>'
             '<path d="M8 19h.01M12 21h.01M16 19h.01"/>',
}

DRINK_ICONS = ("espresso", "milk", "mug", "tea", "iced")
DEFAULT_DRINK_ICON = "espresso"


def render_icon(name, size=24, label=None, cls=""):
    """
    Purpose: Draws one icon of the set as inline SVG for a template.

    Args:
        name (str): Key of ICON_PATHS; unknown names draw nothing.
        size (int): Width and height in px.
        label (str | None): Accessible name; None marks the icon as
            decorative (hidden from screen readers).
        cls (str): Extra CSS classes.

    Returns:
        Markup: The <svg> element, safe to insert into HTML.
    """
    paths = ICON_PATHS.get(name)
    if paths is None:
        return Markup("")
    if label:
        a11y = f'role="img" aria-label="{escape(label)}"'
    else:
        a11y = 'aria-hidden="true" focusable="false"'
    return Markup(
        f'<svg class="icon {escape(cls)}" width="{int(size)}" height="{int(size)}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round" {a11y}>{paths}</svg>'
    )


def day_greeting(now=None):
    """
    Purpose: Picks the greeting for the home screen from the machine clock
        (which follows the simulated time of day when one is set).

    Args:
        now (datetime | None): Time to use; defaults to the machine clock.

    Returns:
        str: "Good morning", "Good afternoon" or "Good evening".
    """
    if now is None:
        now = clock.now()
    if 5 <= now.hour < 12:
        return "Good morning"
    if 12 <= now.hour < 17:
        return "Good afternoon"
    return "Good evening"


def drink_icon_name(drink):
    """
    Purpose: Picks the icon for a drink from its stored type.

    Args:
        drink (Drink): The drink.

    Returns:
        str: One of DRINK_ICONS.
    """
    return drink.icon if drink.icon in DRINK_ICONS else DEFAULT_DRINK_ICON


def render_drink_icon(drink, size=56):
    """
    Purpose: Draws a drink's icon inside the standard drink-icon wrapper.

    Args:
        drink (Drink): The drink.
        size (int): Icon size in px.

    Returns:
        Markup: <span class="drink-icon" data-icon="…"><svg…></span>.
    """
    name = drink_icon_name(drink)
    return Markup(f'<span class="drink-icon" data-icon="{name}">') + render_icon(name, size) + Markup("</span>")
