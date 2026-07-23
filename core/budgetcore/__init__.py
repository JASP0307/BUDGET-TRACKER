"""budgetcore — pure domain logic for the budget tracker.

Email parsing, categorization, dedupe signatures, and notification message
builders. No I/O, no credentials, no framework dependencies (only bs4).
Shared by the legacy cron pipeline and the web app.
"""

__version__ = "0.2.0"
