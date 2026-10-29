# SmartBrew AI – Project Rules

This is a university prototype (Lab Assignment 3, Task B) of SmartBrew AI, an
AI-powered coffee machine. The app runs in a web browser and imitates the
machine's touchscreen. Requirement IDs (FR-xx, NFR-xx) come from the Task A
requirements document.

## Tech stack
- Python 3.12, Flask (app factory pattern), Jinja2 templates
- SQLite through Flask-SQLAlchemy (database file in `data/`)
- bcrypt for password and PIN hashing (NFR-09)
- qrcode[pil] for QR receipts
- pytest for tests
- Docker + docker-compose for running anywhere
- Pin every dependency with an exact version (`==`) in `requirements.txt`

## Folder structure
```
app/
  __init__.py          # app factory
  models.py            # SQLAlchemy models
  routes/              # blueprints: auth, customer, admin, simulation
  services/            # business logic: inventory, recommendations,
                       # payments, connectivity, receipts, weather
  templates/           # Jinja2 templates
  static/              # CSS, JS, generated QR images
data/                  # SQLite database and email outbox (git-ignored)
tests/                 # one test file per feature
docs/llm-conversations/
seed.py                # creates demo data
run.py                 # starts the app
```

## The 10 features to build
1. Accounts and login: create account, log in with password or simulated
   NFC card tap, session ends after 60 s inactivity or after a purchase
   (FR-01–03, NFR-09, NFR-14)
2. Menu browsing by category with nutritional information (FR-11, FR-12)
3. Live drink customization (milk, strength, temperature, syrup shots) with
   nutrition and price updating, and depleted options blocked (FR-13, FR-65)
4. Inventory-aware ordering: block drinks below minimum threshold, deduct
   stock on dispense, re-check before dispensing (FR-14, FR-26–28, FR-66)
5. Saved recipes: save, name, edit, one-touch order with stock check
   (FR-07–10, FR-15)
6. Personalized recommendations: score from time of day, weather and order
   history; reorder menu and highlight top 3; work without weather
   (FR-16–18, FR-22–25, FR-56)
7. Payments: card (chip/contactless), Apple Pay/Google Pay, prepaid employee
   card with balance; handle declines; never charge twice (FR-31–35, FR-62)
8. Offline fallback: detect network/service failure, disable unavailable
   payment methods, queue offline transactions, sync when back online
   (FR-53–55, FR-58, FR-59)
9. Receipts: QR code shown on screen and email receipt; emails queued while
   offline (FR-36, FR-37, FR-60)
10. Admin mode: access by PIN (lockout after 5 wrong tries for 15 min) or
    NFC master card; refill/reset ingredient counters; waste bin monitoring
    with alert at 80% and halt at 95%; error dashboard and event log
    (FR-43–52, NFR-12, NFR-31, NFR-41)

Transaction history (FR-05, FR-06) is recorded for every completed order.

## Simulated hardware and services
There is no real hardware. Everything is simulated and controllable from a
**Simulation panel** page:
- NFC card tap: choose a demo card from a list
- Payments: fake processor; specific demo card numbers are declined
- Network on/off, weather API up/down
- Waste bin levels, and faults such as "Grinder Jammed" or "Water Heater Fault"
- Weather: a mock service returning weather for the time of day; can fail
- Email: written as files to `data/outbox/` instead of being sent
Never call real external APIs and never require API keys.

## Code documentation rule (mandatory, graded)
Every function and method, including small helpers, routes and `__init__`,
must have a docstring in this format:

```python
def calculate_score(drink, user, now, weather):
    """
    Purpose: Calculates the recommendation score of a drink for a user.

    Args:
        drink (Drink): The drink to score.
        user (User | None): The logged-in user, or None for a guest.
        now (datetime): Current date and time.
        weather (dict | None): Current weather, or None if unavailable.

    Returns:
        float: Score between 0 and 100; higher means more recommended.
    """
```
Add short inline comments for non-obvious logic and mention the related
requirement ID (e.g. `# FR-66: re-check stock before dispensing`).

## Working rules
- Before writing code for a new feature, show a short plan and wait for
  approval.
- Keep business logic in `services/`, not in routes or templates.
- Write pytest tests for each feature in `tests/test_featureNN_<name>.py`.
- Code must run on Windows, macOS and Linux, and inside Docker.
- Keep the UI simple, clean and touchscreen-like (large buttons).
- Do not commit the database, `.venv/` or `data/outbox/`.
