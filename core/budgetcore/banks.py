"""Registry of supported banks — the single source of truth for each bank's
notification sender and display name.

Everything downstream derives from ``SUPPORTED_BANKS``: the parser dispatch
(``parsers.parse_email``), the inbound spoofing guard (``ingest``), and the
onboarding filter + bank picker (``setup``). Adding a bank is one entry here
plus a parser function wired in ``parsers._PARSERS`` — nothing downstream
changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Bank


@dataclass(frozen=True)
class BankSpec:
    bank: Bank
    display_name: str  # shown in the UI, e.g. "Banco Popular"
    sender: str        # the exact From address the bank notifies from

    @property
    def domain(self) -> str:
        return self.sender.split("@", 1)[1]


SUPPORTED_BANKS: tuple[BankSpec, ...] = (
    BankSpec(Bank.POPULAR, "Banco Popular", "notificaciones@popularenlinea.com"),
    BankSpec(Bank.QIK, "Qik", "notificaciones@qik.do"),
    # BHD notifies card/account movement from Alertas@; the transfer alerts are
    # the ones we log as spend (see parsers._parse_bhd).
    BankSpec(Bank.BHD, "Banco BHD", "alertas@bhd.com.do"),
    # Banreservas sends card-consumption alerts from notificaciones@. Its App
    # receipts (NotificacionesTuBancoApp@ — same domain, so the same parser)
    # are card payments and transfers, which _parse_banreservas ignores.
    BankSpec(Bank.BANRESERVAS, "Banreservas", "notificaciones@banreservas.com"),
)


def bank_domains() -> tuple[str, ...]:
    """Sender domains — used to guard against spoofed inbound mail."""
    return tuple(s.domain for s in SUPPORTED_BANKS)


def bank_choices() -> list[tuple[str, str]]:
    """(id, display name) pairs for the onboarding bank picker."""
    return [(s.bank.value, s.display_name) for s in SUPPORTED_BANKS]


def from_query() -> str:
    """The Gmail ``from:(...)`` value listing every supported bank's sender,
    shared by the copyable search and the downloadable filter."""
    return " OR ".join(s.sender for s in SUPPORTED_BANKS)
