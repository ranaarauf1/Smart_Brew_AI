# LLM conversations

The full Claude Code conversations used to build SmartBrew AI (model:
Claude Opus 5.5), in order. Each one starts with my prompt and includes
the plans, my approvals and corrections, the code changes and test runs.

| File | What it covers |
|---|---|
| [01-scaffold.md](01-scaffold.md) | Project scaffold: folder structure, app factory, all database models, demo data (`seed.py`), base layout, Docker setup, first smoke test |
| [02-accounts-menu.md](02-accounts-menu.md) | Feature 1 (accounts, password and NFC login, 60 s session timeout, profile) and feature 2 (menu by category, nutrition) |
| [03-customization-inventory.md](03-customization-inventory.md) | Feature 3 (live customization, out-of-stock options) and feature 4 (inventory checks, dispensing, low-stock events) |
| [04-recipes-recommendations.md](04-recipes-recommendations.md) | Feature 5 (saved recipes, one-touch ordering) and feature 6 (recommendations, weather and time simulation, preferences) |
| [05-payments-offline.md](05-payments-offline.md) | Feature 7 (payments, declines, never charging twice, refunds) and feature 8 (offline mode, store-and-forward, sync, dropped connection) |
| [06-receipts-admin.md](06-receipts-admin.md) | Feature 9 (QR and email receipts) and feature 10 (service mode, PIN lockout, audit log, waste bins, faults, event log) |
| [07-ui-redesign.md](07-ui-redesign.md) | Visual redesign: design tokens, fonts, SVG icons, the live cup, the premium espresso machine look, plain Simulation tool and service mode, screenshots |
| 08-readme-submission.md | README, this index, final review (pinned requirements, cleanup), Docker check and walkthrough verification – to be exported at the end of this conversation |
