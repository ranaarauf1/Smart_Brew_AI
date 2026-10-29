"""The cup picture on the customization screen (presentation only).

Turns the ingredient quantities of the customized drink (from
customization.compute_ingredients) into the layers drawn in the cup, so the
picture follows the same Python calculation as price and nutrition (FR-13).
JavaScript only redraws what this returns.

===========================================================================
CUP RULES (presentation, see README)
===========================================================================
Layers, bottom to top: syrup, chocolate, espresso, tea, milk, foam.
  * syrup:     the syrup quantity (10 ml per shot)
  * chocolate: chocolate powder × CHOCOLATE_ML_PER_GRAM (melted)
  * espresso:  the water brewed through the beans (coffee drinks)
  * tea:       tea water, or the chai concentrate
  * milk/foam: the chosen milk, split by the drink's FOAM_SHARE
Hot drinks are drawn in a porcelain cup (CUP_CAPACITY_ML), iced drinks in a
glass (GLASS_CAPACITY_ML) with ice. Steam: warm 1, hot 2, extra hot 3.
===========================================================================
"""

from app.models import Drink, Ingredient

CUP_CAPACITY_ML = 400
GLASS_CAPACITY_ML = 450
CHOCOLATE_ML_PER_GRAM = 3

# Share of the milk shown as foam on top, by drink (others: DEFAULT_FOAM_SHARE)
FOAM_SHARE = {
    "Cappuccino": 0.40,
    "Latte": 0.15,
    "Flat White": 0.08,
    "Mocha": 0.12,
    "Hot Chocolate": 0.10,
    "Chai Latte": 0.12,
    "Iced Latte": 0.0,
}
DEFAULT_FOAM_SHARE = 0.12

STEAM_BY_TEMPERATURE = {"Warm": 1, "Hot": 2, "Extra hot": 3}
DEFAULT_STEAM = 2

LAYER_ORDER = ("syrup", "chocolate", "espresso", "tea", "milk", "foam")


def describe_cup(drink, selection, needs):
    """
    Purpose: Describes the cup picture for a customized drink: vessel,
        layers with their millilitres, ice and steam.

    Args:
        drink (Drink): The drink.
        selection (Selection): Validated choices (for names of milk, syrup
            and temperature).
        needs (dict[Ingredient, float]): Ingredient quantities, as returned
            by customization.compute_ingredients().

    Returns:
        dict: Keys vessel ("cup" or "glass"), capacity_ml, layers (list of
            {"kind", "label", "ml"} in LAYER_ORDER, only layers above 0 ml),
            ice (bool), steam (int) and description (text for screen readers).
    """
    amounts = {kind: 0.0 for kind in LAYER_ORDER}
    labels = {"syrup": "Syrup", "chocolate": "Chocolate", "espresso": "Espresso",
              "tea": "Tea", "milk": "Milk", "foam": "Foam"}
    has_beans = any(i.kind == Ingredient.KIND_COFFEE for i in needs)

    for ingredient, quantity in needs.items():
        if ingredient.kind == Ingredient.KIND_SYRUP:
            amounts["syrup"] += quantity
        elif ingredient.kind == Ingredient.KIND_MILK:
            amounts["milk"] += quantity
            labels["milk"] = ingredient.name
        elif ingredient.name == "Chocolate powder":
            amounts["chocolate"] += quantity * CHOCOLATE_ML_PER_GRAM
        elif ingredient.kind == Ingredient.KIND_WATER:
            amounts["espresso" if has_beans else "tea"] += quantity
        elif ingredient.name == "Chai concentrate":
            amounts["tea"] += quantity

    if selection.syrup is not None:
        labels["syrup"] = f"{selection.syrup.name} syrup"
    if amounts["espresso"] and drink.name in ("Americano", "Cold Brew"):
        labels["espresso"] = "Coffee"

    foam_share = FOAM_SHARE.get(drink.name, DEFAULT_FOAM_SHARE)
    amounts["foam"] = round(amounts["milk"] * foam_share)
    amounts["milk"] -= amounts["foam"]

    iced = drink.serving == Drink.SERVING_ICED
    steam = 0
    if not iced:
        temperature = selection.temperature
        steam = STEAM_BY_TEMPERATURE.get(temperature.name, DEFAULT_STEAM) if temperature else DEFAULT_STEAM

    layers = [
        {"kind": kind, "label": labels[kind], "ml": round(amounts[kind])}
        for kind in LAYER_ORDER
        if round(amounts[kind]) > 0
    ]
    parts = ", ".join(f"{layer['ml']} ml {layer['label'].lower()}" for layer in layers)
    vessel = "glass" if iced else "cup"
    return {
        "vessel": vessel,
        "capacity_ml": GLASS_CAPACITY_ML if iced else CUP_CAPACITY_ML,
        "layers": layers,
        "ice": iced,
        "steam": steam,
        "description": f"{'Glass' if iced else 'Cup'}: {parts or 'empty'}"
                       + (", with ice" if iced else ""),
    }


def layer_kinds():
    """
    Purpose: Lists every layer kind in drawing order (bottom to top), for
        the template that draws all of them and hides the empty ones.

    Args:
        None

    Returns:
        tuple[str, ...]: LAYER_ORDER.
    """
    return LAYER_ORDER
