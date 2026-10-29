"""SQLAlchemy models for SmartBrew AI.

All money values are stored as integer cents (euros) to avoid float rounding.
All timestamps are stored as naive UTC datetimes.
"""

from datetime import datetime, timezone

from app import db


def utcnow():
    """
    Purpose: Returns the current UTC time without tzinfo, the form SQLite stores.

    Args:
        None

    Returns:
        datetime: Current UTC date and time (naive).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Accounts and NFC (feature 1, feature 10)
# ---------------------------------------------------------------------------


class User(db.Model):
    """A registered customer account (FR-01–03)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)  # bcrypt, NFR-09
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    nfc_cards = db.relationship("NfcCard", back_populates="user")
    saved_recipes = db.relationship(
        "SavedRecipe", back_populates="user", cascade="all, delete-orphan"
    )
    transactions = db.relationship("Transaction", back_populates="user")
    preference = db.relationship(
        "UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the user.
        """
        return f"<User {self.id} {self.email}>"


class NfcCard(db.Model):
    """A simulated NFC card; linked to a user, or a master card for admin mode."""

    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(32), unique=True, nullable=False)
    label = db.Column(db.String(80), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # None = not linked
    is_master = db.Column(db.Boolean, nullable=False, default=False)  # admin mode access (FR-43–52)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", back_populates="nfc_cards")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the card.
        """
        return f"<NfcCard {self.uid} master={self.is_master}>"


class UserPreference(db.Model):
    """A user's preference profile; created empty with the account (feature 1),
    filled in by customization and recommendations (features 3, 6)."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    preferred_milk_option_id = db.Column(db.Integer, db.ForeignKey("customization_option.id"))
    preferred_strength_option_id = db.Column(db.Integer, db.ForeignKey("customization_option.id"))
    preferred_temperature_option_id = db.Column(
        db.Integer, db.ForeignKey("customization_option.id")
    )
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="preference")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the preference profile.
        """
        return f"<UserPreference user={self.user_id}>"


# ---------------------------------------------------------------------------
# Menu, recipes and inventory (features 2, 3, 4)
# ---------------------------------------------------------------------------


class Category(db.Model):
    """A menu category such as "Milk Coffees" (FR-11, FR-12)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    drinks = db.relationship("Drink", back_populates="category")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the category.
        """
        return f"<Category {self.name}>"


class Drink(db.Model):
    """A drink on the menu with base price and nutrition per serving (FR-11, FR-12)."""

    SERVING_HOT = "hot"
    SERVING_ICED = "iced"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=False, default="")
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    base_price_cents = db.Column(db.Integer, nullable=False)
    serving = db.Column(db.String(10), nullable=False, default=SERVING_HOT)
    calories = db.Column(db.Integer, nullable=False, default=0)
    sugar_g = db.Column(db.Float, nullable=False, default=0)
    fat_g = db.Column(db.Float, nullable=False, default=0)
    protein_g = db.Column(db.Float, nullable=False, default=0)
    caffeine_mg = db.Column(db.Integer, nullable=False, default=0)
    icon = db.Column(db.String(8), nullable=False, default="☕")  # emoji shown on the menu
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    category = db.relationship("Category", back_populates="drinks")
    recipe_items = db.relationship(
        "DrinkIngredient", back_populates="drink", cascade="all, delete-orphan"
    )

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the drink.
        """
        return f"<Drink {self.name}>"


class Ingredient(db.Model):
    """A consumable in the machine with its current stock (FR-14, FR-26–28)."""

    KIND_COFFEE = "coffee"
    KIND_WATER = "water"
    KIND_MILK = "milk"
    KIND_SYRUP = "syrup"
    KIND_OTHER = "other"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    kind = db.Column(db.String(20), nullable=False, default=KIND_OTHER)
    unit = db.Column(db.String(10), nullable=False)  # g, ml or pcs
    stock_level = db.Column(db.Float, nullable=False, default=0)
    min_threshold = db.Column(db.Float, nullable=False, default=0)  # FR-14, FR-26–28: block drink below this
    capacity = db.Column(db.Float, nullable=False)  # refill/reset target (FR-43–52)
    # Nutrition per 100 units (g or ml), used to calculate the effect of
    # customizations such as a milk swap or an extra shot (FR-13)
    kcal_per_100 = db.Column(db.Float, nullable=False, default=0)
    sugar_per_100 = db.Column(db.Float, nullable=False, default=0)
    fat_per_100 = db.Column(db.Float, nullable=False, default=0)
    protein_per_100 = db.Column(db.Float, nullable=False, default=0)
    caffeine_per_100 = db.Column(db.Float, nullable=False, default=0)  # mg

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the ingredient and its stock.
        """
        return f"<Ingredient {self.name} {self.stock_level}{self.unit}>"


class DrinkIngredient(db.Model):
    """One line of a drink's recipe: how much of an ingredient one serving uses."""

    id = db.Column(db.Integer, primary_key=True)
    drink_id = db.Column(db.Integer, db.ForeignKey("drink.id"), nullable=False)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredient.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)

    drink = db.relationship("Drink", back_populates="recipe_items")
    ingredient = db.relationship("Ingredient")

    __table_args__ = (db.UniqueConstraint("drink_id", "ingredient_id"),)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the recipe line.
        """
        return f"<DrinkIngredient drink={self.drink_id} ingredient={self.ingredient_id} qty={self.quantity}>"


class CustomizationOption(db.Model):
    """A selectable customization such as oat milk or a vanilla shot (FR-13).

    Milk options replace the milk in the drink's recipe with their ingredient
    (no ingredient = "None", the milk is left out). Strength and syrup options
    add ``ingredient_qty`` (can be negative, e.g. fewer beans for "Mild");
    syrup quantity and price are per shot. Nutrition changes are calculated
    from the ingredients' nutrition values, not stored here.
    """

    GROUP_MILK = "milk"
    GROUP_STRENGTH = "strength"
    GROUP_TEMPERATURE = "temperature"
    GROUP_SYRUP = "syrup"

    id = db.Column(db.Integer, primary_key=True)
    group = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    price_delta_cents = db.Column(db.Integer, nullable=False, default=0)
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredient.id"))
    ingredient_qty = db.Column(db.Float, nullable=False, default=0)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    ingredient = db.relationship("Ingredient")

    __table_args__ = (db.UniqueConstraint("group", "name"),)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the option.
        """
        return f"<CustomizationOption {self.group}:{self.name}>"


class SavedRecipe(db.Model):
    """A user's named, customized drink for one-touch ordering (FR-07–10, FR-15).

    ``customizations`` is a selection dict, e.g.
    ``{"milk": 3, "strength": 6, "temperature": 8, "syrup": 10, "syrup_shots": 2}``
    mapping groups to CustomizationOption ids (None where a group does not
    apply or no syrup is chosen). See services/customization.py.
    """

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    drink_id = db.Column(db.Integer, db.ForeignKey("drink.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    customizations = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="saved_recipes")
    drink = db.relationship("Drink")

    __table_args__ = (db.UniqueConstraint("user_id", "name"),)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the saved recipe.
        """
        return f"<SavedRecipe {self.name} user={self.user_id}>"


# ---------------------------------------------------------------------------
# Payments, transactions and offline queue (features 7, 8, 9)
# ---------------------------------------------------------------------------


class Transaction(db.Model):
    """One drink order: its lifecycle, price and the successful payment's
    summary; also the user's order history (FR-05, FR-06).

    Status flow (see services/payments.py and services/orders.py)::

        pending -> paying -> paid -> dispensing -> completed
                         |                    \\-> cancelled (refunded, FR-66)
                         \\-> payment_unknown (connection dropped, FR-62)
    """

    STATUS_PENDING = "pending"  # waiting for payment
    STATUS_PAYING = "paying"  # claimed by one payment request (FR-62: no double charge)
    STATUS_PAYMENT_UNKNOWN = "payment_unknown"  # connection dropped mid-payment
    STATUS_PAID = "paid"  # approved or accepted offline, not yet dispensed
    STATUS_DISPENSING = "dispensing"  # claimed by one dispense request (no double dispense)
    STATUS_COMPLETED = "completed"  # drink dispensed, stock deducted
    STATUS_CANCELLED = "cancelled"  # FR-66: stock re-check failed, payment refunded

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # None = guest
    drink_id = db.Column(db.Integer, db.ForeignKey("drink.id"), nullable=False)
    customizations = db.Column(db.JSON, nullable=False, default=dict)
    amount_cents = db.Column(db.Integer, nullable=False)
    # Summary of the successful payment (details in Payment); NFR-08: brand
    # and last 4 digits only, never the full card number
    payment_method = db.Column(db.String(20))
    card_brand = db.Column(db.String(20))
    card_last4 = db.Column(db.String(4))
    prepaid_card_id = db.Column(db.Integer, db.ForeignKey("prepaid_card.id"))
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    failure_reason = db.Column(db.String(255))
    # FR-62: unique key per checkout attempt so a retry can never charge twice
    idempotency_key = db.Column(db.String(64), unique=True, nullable=False)
    receipt_token = db.Column(db.String(64), unique=True)  # used in the QR receipt URL
    receipt_email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="transactions")
    drink = db.relationship("Drink")
    prepaid_card = db.relationship("PrepaidCard")
    payments = db.relationship("Payment", back_populates="transaction", order_by="Payment.id")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the transaction.
        """
        return f"<Transaction {self.id} {self.status} {self.amount_cents}c>"


class PrepaidCard(db.Model):
    """A prepaid employee card with a stored balance (FR-31–35)."""

    id = db.Column(db.Integer, primary_key=True)
    card_number = db.Column(db.String(32), unique=True, nullable=False)
    holder_name = db.Column(db.String(80), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    balance_cents = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the card and balance.
        """
        return f"<PrepaidCard {self.card_number} {self.balance_cents}c>"


class Payment(db.Model):
    """One payment attempt for an order (FR-31–35). An order has at most one
    successful payment (FR-62). NFR-08: only brand and last 4 digits are
    stored, never the full card number."""

    METHOD_CHIP = "card_chip"  # "Insert card"
    METHOD_CONTACTLESS = "card_contactless"  # "Tap card"
    METHOD_APPLE_PAY = "apple_pay"
    METHOD_GOOGLE_PAY = "google_pay"
    METHOD_PREPAID = "prepaid"
    CARD_METHODS = (METHOD_CHIP, METHOD_CONTACTLESS)
    WALLET_METHODS = (METHOD_APPLE_PAY, METHOD_GOOGLE_PAY)
    ALL_METHODS = CARD_METHODS + WALLET_METHODS + (METHOD_PREPAID,)

    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_ERROR = "error"  # processor error, not charged
    STATUS_OFFLINE_QUEUED = "offline_queued"  # FR-54: accepted offline, waiting to be sent
    STATUS_SYNCED = "synced"  # FR-55: offline payment approved on sync
    STATUS_SYNC_DECLINED = "sync_declined"  # offline payment declined on sync (a loss)
    STATUS_REFUNDED = "refunded"  # dispense failed after payment
    STATUS_VOIDED = "voided"  # offline payment cancelled before it was sent
    STATUS_UNKNOWN = "unknown"  # connection dropped, outcome checked on reconnect
    STATUS_FAILED = "failed"  # connection dropped and the bank never charged
    # Statuses that mean "this order is paid"
    SUCCESS_STATUSES = (STATUS_APPROVED, STATUS_OFFLINE_QUEUED, STATUS_SYNCED)

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    card_brand = db.Column(db.String(20))
    card_last4 = db.Column(db.String(4))
    prepaid_card_id = db.Column(db.Integer, db.ForeignKey("prepaid_card.id"))
    amount_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    error_code = db.Column(db.String(10))  # NFR-27, e.g. PAY-101
    # FR-62: sent to the processor; the same key can never be charged twice
    idempotency_key = db.Column(db.String(64), unique=True, nullable=False)
    processor_reference = db.Column(db.String(40))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    transaction = db.relationship("Transaction", back_populates="payments")
    prepaid_card = db.relationship("PrepaidCard")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the payment.
        """
        return f"<Payment {self.id} {self.method} {self.status} {self.amount_cents}c>"


class ProcessorCharge(db.Model):
    """SIMULATION ONLY: the external card/wallet processor's own records.

    This table stands in for the bank's side, which the machine cannot see
    directly. It lets the simulation (and the tests) prove that a customer
    was charged at most once per idempotency key (FR-62).
    """

    PROCESSOR_CARD = "card"
    PROCESSOR_WALLET = "wallet"

    STATUS_CHARGED = "charged"
    STATUS_REFUNDED = "refunded"

    id = db.Column(db.Integer, primary_key=True)
    processor = db.Column(db.String(10), nullable=False)
    idempotency_key = db.Column(db.String(64), unique=True, nullable=False)
    token = db.Column(db.String(40), nullable=False)  # stand-in card token, not a card number
    amount_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(10), nullable=False, default=STATUS_CHARGED)
    reference = db.Column(db.String(40), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    refunded_at = db.Column(db.DateTime)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the simulated charge.
        """
        return f"<ProcessorCharge {self.reference} {self.status} {self.amount_cents}c>"


class OfflineQueueItem(db.Model):
    """Work stored while offline and sent on reconnect (FR-53–55, FR-60).
    Items are never deleted: each ends in a final status (NFR-29)."""

    KIND_TRANSACTION = "transaction"
    KIND_EMAIL = "email"

    STATUS_QUEUED = "queued"
    STATUS_SYNCED = "synced"
    STATUS_DECLINED = "declined"  # sent, but the bank declined it
    STATUS_VOIDED = "voided"  # cancelled before sending (e.g. drink not dispensed)

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"))
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"))
    status = db.Column(db.String(20), nullable=False, default=STATUS_QUEUED)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    synced_at = db.Column(db.DateTime)

    transaction = db.relationship("Transaction")
    payment = db.relationship("Payment")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the queue item.
        """
        return f"<OfflineQueueItem {self.id} {self.kind} {self.status}>"


# ---------------------------------------------------------------------------
# Maintenance and admin (feature 10)
# ---------------------------------------------------------------------------


class WasteBin(db.Model):
    """A waste container whose fill level is monitored (FR-43–52)."""

    ALERT_LEVEL = 80  # percent: show maintenance alert
    HALT_LEVEL = 95  # percent: stop dispensing

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    level_percent = db.Column(db.Float, nullable=False, default=0)
    emptied_at = db.Column(db.DateTime)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the bin and its level.
        """
        return f"<WasteBin {self.name} {self.level_percent}%>"


class ErrorEvent(db.Model):
    """A machine fault or error shown on the error dashboard (FR-43–52)."""

    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_CRITICAL = "critical"

    CODE_LOW_INGREDIENT = "LOW_INGREDIENT_THRESHOLD"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), nullable=False)  # e.g. GRINDER_JAMMED
    component = db.Column(db.String(40), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    severity = db.Column(db.String(10), nullable=False, default=SEVERITY_WARNING)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime)  # None = still active
    ingredient_id = db.Column(db.Integer, db.ForeignKey("ingredient.id"))  # for stock events

    ingredient = db.relationship("Ingredient")

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the error event.
        """
        return f"<ErrorEvent {self.code} {self.severity}>"


class AdminAuditLog(db.Model):
    """Record of every admin login attempt and admin action (FR-43–52)."""

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(60), nullable=False)  # e.g. LOGIN_OK, REFILL
    actor = db.Column(db.String(60), nullable=False)  # "PIN" or NFC card uid
    details = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the log entry.
        """
        return f"<AdminAuditLog {self.action} by {self.actor}>"


class SystemState(db.Model):
    """Single-row table with simulation switches and admin lockout state."""

    SINGLETON_ID = 1

    WEATHER_AUTO = "auto"  # mock weather follows the time of day
    WEATHER_MANUAL = "manual"  # values set on the Simulation panel

    id = db.Column(db.Integer, primary_key=True)
    network_online = db.Column(db.Boolean, nullable=False, default=True)
    weather_online = db.Column(db.Boolean, nullable=False, default=True)  # FR-56 switch
    weather_mode = db.Column(db.String(10), nullable=False, default=WEATHER_AUTO)
    weather_condition = db.Column(db.String(10), nullable=False, default="cloudy")
    weather_temp_c = db.Column(db.Float, nullable=False, default=12)
    time_override = db.Column(db.String(5))  # "HH:MM" simulated time, None = real time
    # Feature 8: simulated outside services (FR-53–55, FR-58)
    card_processor_online = db.Column(db.Boolean, nullable=False, default=True)
    wallet_processor_online = db.Column(db.Boolean, nullable=False, default=True)
    drop_next_payment = db.Column(db.String(15), nullable=False, default="off")  # off/before_charge/after_charge
    last_sync_at = db.Column(db.DateTime)
    last_sync_summary = db.Column(db.String(255))
    admin_pin_hash = db.Column(db.String(128), nullable=False)  # bcrypt, NFR-09
    failed_pin_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)  # 15 min lockout after 5 wrong PINs (FR-43–52)

    @classmethod
    def get(cls):
        """
        Purpose: Loads the single system state row.

        Args:
            None

        Returns:
            SystemState | None: The state row, or None if the database is not seeded.
        """
        return db.session.get(cls, cls.SINGLETON_ID)

    def __repr__(self):
        """
        Purpose: Gives a readable representation for debugging.

        Args:
            None

        Returns:
            str: Short description of the switches.
        """
        return f"<SystemState network={self.network_online} weather={self.weather_online}>"
