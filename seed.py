"""Creates SmartBrew AI demo data. WARNING: drops and recreates all tables.

Usage:
    python seed.py

===========================================================================
DEMO CREDENTIALS (all fake, for the prototype only)
===========================================================================
Customer accounts (email / password):
    Alice Example   alice@demo.com   Alice2026!
    Bob Example     bob@demo.com     Bob2026!

Simulated NFC cards (uid / meaning):
    04A1B2C3   Alice's card          -> logs in as Alice
    04B2C3D4   Bob's card            -> logs in as Bob
    04FFEE01   Technician master     -> opens admin mode
    04DEAD99   Unregistered card     -> not linked to any account

Technician PIN (admin mode):
    1234       (5 wrong tries -> locked for 15 minutes)

Prepaid employee card:
    PP-1000-0001   holder Alice Example   balance EUR 25.00

Payment test card numbers (fake processor, implemented with feature 7):
    4242 4242 4242 4242   approved
    4000 0000 0000 0002   declined: card declined
    4000 0000 0000 9995   declined: insufficient funds

Demo order history (for recommendations, feature 6):
    Alice: 3x Flat White and 1x Latte, oat milk, mostly strong
    Bob:   no orders (shows recommendations from time and weather only)
===========================================================================
"""

import uuid
from datetime import timedelta

from app import create_app, db
from app.models import (
    Category,
    CustomizationOption,
    Drink,
    DrinkIngredient,
    Ingredient,
    NfcCard,
    PrepaidCard,
    SystemState,
    Transaction,
    User,
    UserPreference,
    WasteBin,
    utcnow,
)
from app.services import customization, orders, preferences
from app.services.security import hash_secret

TECHNICIAN_PIN = "1234"

# name: (kind, unit, stock_level, min_threshold, capacity)
INGREDIENTS = {
    "Coffee beans": (Ingredient.KIND_COFFEE, "g", 1800, 200, 2000),
    "Water": (Ingredient.KIND_WATER, "ml", 9000, 1000, 10000),
    "Whole milk": (Ingredient.KIND_MILK, "ml", 4000, 500, 5000),
    "Skimmed milk": (Ingredient.KIND_MILK, "ml", 3000, 500, 3000),
    "Oat milk": (Ingredient.KIND_MILK, "ml", 2500, 500, 3000),
    # Deliberately below its threshold so "Out of stock" can be demonstrated
    "Almond milk": (Ingredient.KIND_MILK, "ml", 300, 500, 3000),
    "Chocolate powder": (Ingredient.KIND_OTHER, "g", 900, 100, 1000),
    "Chai concentrate": (Ingredient.KIND_OTHER, "ml", 1500, 200, 2000),
    "Green tea leaves": (Ingredient.KIND_OTHER, "g", 400, 20, 500),
    "Vanilla syrup": (Ingredient.KIND_SYRUP, "ml", 700, 50, 1000),
    "Caramel syrup": (Ingredient.KIND_SYRUP, "ml", 600, 50, 1000),
    "Ice": (Ingredient.KIND_OTHER, "g", 4000, 500, 5000),
    "Cups": (Ingredient.KIND_OTHER, "pcs", 180, 20, 200),
}

# Nutrition per 100 g/ml: (kcal, sugar g, fat g, protein g, caffeine mg).
# Only ingredients that customizations change need values; others are 0.
# Coffee beans: contribution of the extracted coffee per 100 g of beans.
INGREDIENT_NUTRITION = {
    "Coffee beans": (30, 0, 2, 1, 700),
    "Whole milk": (64, 4.8, 3.6, 3.3, 0),
    "Skimmed milk": (35, 5.0, 0.1, 3.4, 0),
    "Oat milk": (46, 4.0, 1.5, 1.0, 0),
    "Almond milk": (24, 3.0, 1.1, 0.5, 0),
    "Vanilla syrup": (330, 82, 0, 0, 0),
    "Caramel syrup": (330, 80, 0, 0, 0),
}

# Category -> list of drinks. Nutrition is per serving.
DRINKS = {
    "Espresso Classics": [
        {
            "name": "Espresso",
            "icon": "☕",
            "description": "A short, intense shot of espresso.",
            "price": 220, "calories": 3, "sugar": 0, "fat": 0.2, "protein": 0.1, "caffeine": 63,
            "recipe": {"Coffee beans": 9, "Water": 30, "Cups": 1},
        },
        {
            "name": "Doppio",
            "icon": "☕",
            "description": "A double shot of espresso.",
            "price": 280, "calories": 5, "sugar": 0, "fat": 0.4, "protein": 0.2, "caffeine": 126,
            "recipe": {"Coffee beans": 18, "Water": 60, "Cups": 1},
        },
        {
            "name": "Americano",
            "icon": "☕",
            "description": "Double espresso topped up with hot water.",
            "price": 260, "calories": 5, "sugar": 0, "fat": 0.3, "protein": 0.3, "caffeine": 126,
            "recipe": {"Coffee beans": 18, "Water": 200, "Cups": 1},
        },
    ],
    "Milk Coffees": [
        {
            "name": "Cappuccino",
            "icon": "☕",
            "description": "Espresso with steamed milk and a thick layer of foam.",
            "price": 330, "calories": 110, "sugar": 9, "fat": 6, "protein": 6, "caffeine": 126,
            "recipe": {"Coffee beans": 18, "Water": 60, "Whole milk": 150, "Cups": 1},
        },
        {
            "name": "Latte",
            "icon": "🥛",
            "description": "Espresso with plenty of steamed milk.",
            "price": 350, "calories": 150, "sugar": 13, "fat": 8, "protein": 8, "caffeine": 126,
            "recipe": {"Coffee beans": 18, "Water": 60, "Whole milk": 220, "Cups": 1},
        },
        {
            "name": "Flat White",
            "icon": "🥛",
            "description": "Strong espresso with silky microfoam.",
            "price": 340, "calories": 120, "sugar": 10, "fat": 6.5, "protein": 6.5, "caffeine": 130,
            "recipe": {"Coffee beans": 18, "Water": 40, "Whole milk": 130, "Cups": 1},
        },
        {
            "name": "Mocha",
            "icon": "🍫",
            "description": "Espresso, chocolate and steamed milk.",
            "price": 390, "calories": 250, "sugar": 28, "fat": 10, "protein": 9, "caffeine": 130,
            "recipe": {"Coffee beans": 18, "Water": 60, "Whole milk": 180,
                       "Chocolate powder": 20, "Cups": 1},
        },
    ],
    "Tea & Chocolate": [
        {
            "name": "Hot Chocolate",
            "icon": "🍫",
            "description": "Rich chocolate with steamed milk.",
            "price": 320, "calories": 230, "sugar": 30, "fat": 9, "protein": 8, "caffeine": 10,
            "recipe": {"Chocolate powder": 30, "Whole milk": 220, "Cups": 1},
        },
        {
            "name": "Chai Latte",
            "icon": "🍵",
            "description": "Spiced black tea with steamed milk.",
            "price": 340, "calories": 190, "sugar": 26, "fat": 5, "protein": 6, "caffeine": 50,
            "recipe": {"Chai concentrate": 40, "Whole milk": 200, "Cups": 1},
        },
        {
            "name": "Green Tea",
            "icon": "🍵",
            "description": "Light and refreshing green tea.",
            "price": 240, "calories": 2, "sugar": 0, "fat": 0, "protein": 0.3, "caffeine": 30,
            "recipe": {"Green tea leaves": 2, "Water": 250, "Cups": 1},
        },
    ],
    "Iced Drinks": [
        {
            "name": "Iced Latte",
            "icon": "🧊",
            "description": "Espresso and cold milk over ice.",
            "price": 380, "calories": 130, "sugar": 11, "fat": 7, "protein": 7, "caffeine": 126,
            "serving": Drink.SERVING_ICED,
            "recipe": {"Coffee beans": 18, "Water": 60, "Whole milk": 180, "Ice": 150, "Cups": 1},
        },
        {
            "name": "Cold Brew",
            "icon": "🧊",
            "description": "Slow-steeped coffee served over ice.",
            "price": 360, "calories": 5, "sugar": 0, "fat": 0, "protein": 0.3, "caffeine": 200,
            "serving": Drink.SERVING_ICED,
            "recipe": {"Coffee beans": 25, "Water": 250, "Ice": 150, "Cups": 1},
        },
    ],
}

# (group, name, price_delta_cents, ingredient name or None, ingredient_qty, is_default)
# Milk options replace the recipe's milk ("None" leaves it out); strength adds
# or removes beans; syrup quantity and price are per shot (1–3 shots).
CUSTOMIZATION_OPTIONS = [
    (CustomizationOption.GROUP_MILK, "None", 0, None, 0, False),
    (CustomizationOption.GROUP_MILK, "Whole milk", 0, "Whole milk", 0, True),
    (CustomizationOption.GROUP_MILK, "Skimmed milk", 0, "Skimmed milk", 0, False),
    (CustomizationOption.GROUP_MILK, "Oat milk", 40, "Oat milk", 0, False),
    (CustomizationOption.GROUP_MILK, "Almond milk", 40, "Almond milk", 0, False),
    (CustomizationOption.GROUP_STRENGTH, "Mild", 0, "Coffee beans", -4, False),
    (CustomizationOption.GROUP_STRENGTH, "Regular", 0, None, 0, True),
    (CustomizationOption.GROUP_STRENGTH, "Strong", 30, "Coffee beans", 4, False),
    (CustomizationOption.GROUP_STRENGTH, "Extra shot", 60, "Coffee beans", 9, False),
    (CustomizationOption.GROUP_TEMPERATURE, "Warm", 0, None, 0, False),
    (CustomizationOption.GROUP_TEMPERATURE, "Hot", 0, None, 0, True),
    (CustomizationOption.GROUP_TEMPERATURE, "Extra hot", 0, None, 0, False),
    (CustomizationOption.GROUP_SYRUP, "Vanilla", 50, "Vanilla syrup", 10, False),
    (CustomizationOption.GROUP_SYRUP, "Caramel", 50, "Caramel syrup", 10, False),
]

# Alice's past completed orders: (days ago, drink, {group: option name}).
# Gives her a history for recommendations and a preference profile
# (most frequent: oat milk, strong, hot). Bob has no history.
DEMO_ORDER_HISTORY = [
    (1, "Flat White", {"milk": "Oat milk", "strength": "Strong", "temperature": "Hot"}),
    (2, "Flat White", {"milk": "Oat milk", "strength": "Strong", "temperature": "Hot"}),
    (4, "Latte", {"milk": "Oat milk", "strength": "Regular", "temperature": "Hot"}),
    (6, "Flat White", {"milk": "Oat milk", "strength": "Strong", "temperature": "Hot"}),
]

# (name, email, password, nfc uid)
DEMO_USERS = [
    ("Alice Example", "alice@demo.com", "Alice2026!", "04A1B2C3"),
    ("Bob Example", "bob@demo.com", "Bob2026!", "04B2C3D4"),
]


def seed_ingredients():
    """
    Purpose: Creates all ingredients with their demo stock levels.

    Args:
        None

    Returns:
        dict[str, Ingredient]: Ingredients keyed by name.
    """
    ingredients = {}
    for name, (kind, unit, stock, minimum, capacity) in INGREDIENTS.items():
        kcal, sugar, fat, protein, caffeine = INGREDIENT_NUTRITION.get(name, (0, 0, 0, 0, 0))
        ingredient = Ingredient(
            name=name, kind=kind, unit=unit,
            stock_level=stock, min_threshold=minimum, capacity=capacity,
            kcal_per_100=kcal, sugar_per_100=sugar, fat_per_100=fat,
            protein_per_100=protein, caffeine_per_100=caffeine,
        )
        db.session.add(ingredient)
        ingredients[name] = ingredient
    return ingredients


def seed_menu(ingredients):
    """
    Purpose: Creates the categories, drinks and drink recipes.

    Args:
        ingredients (dict[str, Ingredient]): Ingredients keyed by name.

    Returns:
        int: Number of drinks created.
    """
    count = 0
    for order, (category_name, drinks) in enumerate(DRINKS.items()):
        category = Category(name=category_name, sort_order=order)
        db.session.add(category)
        for data in drinks:
            drink = Drink(
                name=data["name"],
                description=data["description"],
                category=category,
                base_price_cents=data["price"],
                serving=data.get("serving", Drink.SERVING_HOT),
                calories=data["calories"],
                sugar_g=data["sugar"],
                fat_g=data["fat"],
                protein_g=data["protein"],
                caffeine_mg=data["caffeine"],
                icon=data["icon"],
            )
            for ingredient_name, quantity in data["recipe"].items():
                drink.recipe_items.append(
                    DrinkIngredient(ingredient=ingredients[ingredient_name], quantity=quantity)
                )
            db.session.add(drink)
            count += 1
    return count


def seed_customization_options(ingredients):
    """
    Purpose: Creates the milk, strength, temperature and syrup options.

    Args:
        ingredients (dict[str, Ingredient]): Ingredients keyed by name.

    Returns:
        None
    """
    for order, row in enumerate(CUSTOMIZATION_OPTIONS):
        group, name, price, ingredient_name, qty, default = row
        db.session.add(CustomizationOption(
            group=group,
            name=name,
            price_delta_cents=price,
            ingredient=ingredients[ingredient_name] if ingredient_name else None,
            ingredient_qty=qty,
            is_default=default,
            sort_order=order,
        ))


def seed_users_and_cards():
    """
    Purpose: Creates the demo users, their NFC cards, the technician master
        card, an unregistered card and the prepaid employee card.

    Args:
        None

    Returns:
        None
    """
    users = {}
    for name, email, password, uid in DEMO_USERS:
        user = User(name=name, email=email, password_hash=hash_secret(password))
        user.preference = UserPreference()  # empty profile, as for new accounts
        db.session.add(user)
        db.session.add(NfcCard(uid=uid, label=f"{name.split()[0]}'s card", user=user))
        users[email] = user

    db.session.add(NfcCard(uid="04FFEE01", label="Technician master card", is_master=True))
    db.session.add(NfcCard(uid="04DEAD99", label="Unregistered card"))

    db.session.add(PrepaidCard(
        card_number="PP-1000-0001",
        holder_name="Alice Example",
        user=users["alice@demo.com"],
        balance_cents=2500,
    ))


def seed_machine_state():
    """
    Purpose: Creates the waste bins and the system state row with the
        hashed technician PIN.

    Args:
        None

    Returns:
        None
    """
    db.session.add(WasteBin(name="Coffee grounds bin", level_percent=40))
    db.session.add(WasteBin(name="Drip tray", level_percent=20))
    db.session.add(SystemState(
        id=SystemState.SINGLETON_ID,
        network_online=True,
        weather_online=True,
        admin_pin_hash=hash_secret(TECHNICIAN_PIN),  # NFR-09: PIN stored hashed
    ))


def seed_order_history():
    """
    Purpose: Gives Alice a few past completed orders so recommendations
        ("Popular with you") and her preference profile (oat milk, strong)
        can be shown straight away. Past orders do not change the stock.

    Args:
        None

    Returns:
        int: Number of orders created.
    """
    alice = User.query.filter_by(email="alice@demo.com").one()
    now = utcnow()
    for days_ago, drink_name, choices in DEMO_ORDER_HISTORY:
        drink = Drink.query.filter_by(name=drink_name).one()
        data = {
            group: CustomizationOption.query.filter_by(group=group, name=option_name).one().id
            for group, option_name in choices.items()
        }
        selection, errors = customization.parse_selection(drink, data)
        assert not errors, errors  # demo data must always be valid
        needs = customization.compute_ingredients(drink, selection)
        created = now - timedelta(days=days_ago)
        db.session.add(Transaction(
            user=alice,
            drink=drink,
            customizations=orders.build_order_details(drink, selection, needs),
            amount_cents=customization.compute_price(drink, selection),
            status=Transaction.STATUS_COMPLETED,
            idempotency_key=uuid.uuid4().hex,
            created_at=created,
            completed_at=created,
        ))
    db.session.commit()
    preferences.update_preferences(alice)
    return len(DEMO_ORDER_HISTORY)


def seed_database():
    """
    Purpose: Drops all tables, recreates them and fills in the demo data.
        Must be called inside an application context.

    Args:
        None

    Returns:
        int: Number of drinks created.
    """
    db.drop_all()
    db.create_all()
    ingredients = seed_ingredients()
    drink_count = seed_menu(ingredients)
    seed_customization_options(ingredients)
    seed_users_and_cards()
    seed_machine_state()
    db.session.commit()
    seed_order_history()
    return drink_count


def main():
    """
    Purpose: Seeds the configured database and prints a summary.

    Args:
        None

    Returns:
        None
    """
    app = create_app()
    with app.app_context():
        drink_count = seed_database()
    print(f"Seeded {drink_count} drinks in {len(DRINKS)} categories, "
          f"{len(INGREDIENTS)} ingredients and {len(DEMO_USERS)} demo users.")
    print("Demo credentials are listed at the top of seed.py.")


if __name__ == "__main__":
    main()
