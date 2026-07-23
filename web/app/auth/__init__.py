"""Hand-rolled, synchronous auth: Argon2 passwords, signed session cookies
(via Starlette's SessionMiddleware), and signed email-verification tokens.

Deliberately avoids fastapi-users so the app stays on the synchronous
SQLAlchemy layer the rest of the code uses."""
