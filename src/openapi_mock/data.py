"""Realistic value pools and format generators for the mock data engine.

The pools are intentionally small and hand-curated: a mock is judged by whether a
frontend engineer believes the payload, not by whether it contains 10 000 names.
Every generator takes an explicit random.Random so output is reproducible.
"""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import random
import uuid
from collections.abc import Callable
from typing import Any

FIRST_NAMES = (
    "Ada",
    "Grace",
    "Linus",
    "Margaret",
    "Alan",
    "Barbara",
    "Ken",
    "Radia",
    "Guido",
    "Anita",
    "Dennis",
    "Frances",
    "Yukihiro",
    "Katherine",
    "Bjarne",
)
LAST_NAMES = (
    "Lovelace",
    "Hopper",
    "Torvalds",
    "Hamilton",
    "Turing",
    "Liskov",
    "Thompson",
    "Perlman",
    "Rossum",
    "Borg",
    "Ritchie",
    "Allen",
    "Matsumoto",
    "Johnson",
    "Stroustrup",
)
CITIES = (
    "Lisbon",
    "Kyoto",
    "Porto",
    "Tallinn",
    "Osaka",
    "Bergen",
    "Ljubljana",
    "Wellington",
    "Montreal",
    "Ghent",
    "Bristol",
    "Gdansk",
    "Bologna",
    "Aarhus",
)
COUNTRIES = (
    "Portugal",
    "Japan",
    "Estonia",
    "Norway",
    "Slovenia",
    "New Zealand",
    "Canada",
    "Belgium",
    "Poland",
    "Italy",
    "Denmark",
    "Ireland",
)
COUNTRY_CODES = ("PT", "JP", "EE", "NO", "SI", "NZ", "CA", "BE", "PL", "IT", "DK", "IE")
STREETS = (
    "Rua do Sol",
    "Sakura Dori",
    "Kastanienallee",
    "Harbour Road",
    "Rue Verte",
    "Via Roma",
    "Pilies gatve",
    "Fjordveien",
    "King Street",
    "Market Lane",
)
COMPANY_A = ("North", "Blue", "Quiet", "Iron", "Solar", "River", "Bright", "Copper")
COMPANY_B = ("Logistics", "Robotics", "Labs", "Foods", "Systems", "Studio", "Works")
ADJECTIVES = ("Compact", "Rugged", "Modular", "Wireless", "Adjustable", "Stackable")
NOUNS = ("Lamp", "Bottle", "Keyboard", "Backpack", "Monitor", "Chair", "Speaker")
COLORS = ("slate", "amber", "teal", "crimson", "ivory", "navy", "olive")
CURRENCIES = ("EUR", "USD", "JPY", "GBP")
WORDS = (
    "alpha",
    "bravo",
    "cobalt",
    "delta",
    "ember",
    "falcon",
    "garnet",
    "harbor",
    "indigo",
    "juniper",
    "kelp",
    "lumen",
    "meadow",
    "nimbus",
    "onyx",
    "pepper",
)
TAG_WORDS = ("new", "sale", "eco", "limited", "refurbished", "bundle", "pro")
PET_NAMES = ("Biscuit", "Mochi", "Pepper", "Juniper", "Nori", "Clementine", "Basil", "Tofu")
PET_ADJECTIVES = ("Curious", "Sleepy", "Brave", "Gentle", "Smoky", "Golden")


class Factory:
    """Deterministic generator for realistic-looking values."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self._uuid_ns = uuid.UUID(int=seed & ((1 << 128) - 1))
        self._counter = 0

    # -- primitives -----------------------------------------------------
    def pick(self, pool: tuple[str, ...]) -> str:
        return pool[self.rng.randrange(len(pool))]

    def person(self) -> dict[str, str]:
        return {"first": self.pick(FIRST_NAMES), "last": self.pick(LAST_NAMES)}

    def full_name(self) -> str:
        person = self.person()
        return person["first"] + " " + person["last"]

    def email(self) -> str:
        person = self.person()
        return (person["first"] + "." + person["last"]).lower() + "@example.com"

    def company(self) -> str:
        return self.pick(COMPANY_A) + " " + self.pick(COMPANY_B)

    def city(self) -> str:
        return self.pick(CITIES)

    def country_name(self) -> str:
        return self.pick(COUNTRIES)

    def phone(self) -> str:
        return "+351 9" + str(self.integer(10_000_000, 99_999_999))

    def postal_code(self) -> str:
        return str(self.integer(1000, 9999)) + "-" + str(self.integer(100, 999))

    def country_code(self) -> str:
        return self.pick(COUNTRY_CODES)

    def street(self) -> str:
        return str(self.rng.randrange(1, 240)) + " " + self.pick(STREETS)

    def sku(self) -> str:
        return "SKU-" + self.pick(("A", "B", "C", "D")) + str(self.rng.randrange(1000, 9999))

    def product_name(self) -> str:
        return self.pick(ADJECTIVES) + " " + self.pick(NOUNS)

    def pet_name(self) -> str:
        return self.pick(PET_ADJECTIVES) + " " + self.pick(PET_NAMES)

    def word(self) -> str:
        return self.pick(WORDS)

    def sentence(self, words: int = 8) -> str:
        return " ".join(self.pick(WORDS) for _ in range(words)).capitalize() + "."

    # -- formats --------------------------------------------------------
    def uuid(self) -> str:
        self._counter += 1
        return str(uuid.uuid5(self._uuid_ns, "openapi-mock/" + str(self._counter)))

    def date(self, today: dt.date | None = None) -> str:
        base = today or dt.date(2026, 1, 1)
        return (base + dt.timedelta(days=self.rng.randrange(-30, 60))).isoformat()

    def date_time(self, today: dt.datetime | None = None) -> str:
        base = today or dt.datetime(2026, 1, 1, 12, 0, 0)
        moment = base + dt.timedelta(
            days=self.rng.randrange(-30, 60),
            seconds=self.rng.randrange(0, 86_400),
        )
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def ipv4(self) -> str:
        return str(ipaddress.IPv4Address(self.rng.randrange(0x0A000001, 0x0AFFFFFF)))

    def ipv6(self) -> str:
        raw = bytes(self.rng.randrange(256) for _ in range(16))
        return str(ipaddress.IPv6Address(raw))

    def hostname(self) -> str:
        return "api." + self.word() + ".example.com"

    def uri(self) -> str:
        return (
            "https://"
            + self.word()
            + ".example.com/"
            + self.word()
            + "/"
            + str(self.rng.randrange(100, 999))
        )

    def byte(self) -> str:
        raw = bytes(self.rng.randrange(256) for _ in range(12))
        return base64.b64encode(raw).decode("ascii")

    def password(self) -> str:
        return self.word().capitalize() + str(self.rng.randrange(1000, 9999)) + "!"

    def money(self, low: float, high: float) -> float:
        return round(low + self.rng.random() * (high - low), 2)

    def integer(self, low: int, high: int) -> int:
        return self.rng.randint(low, high)


def format_generator(factory: Factory, name: str) -> Callable[[], Any] | None:
    mapping: dict[str, Callable[[], Any]] = {
        "date": factory.date,
        "date-time": factory.date_time,
        "uuid": factory.uuid,
        "email": factory.email,
        "idn-email": factory.email,
        "uri": factory.uri,
        "url": factory.uri,
        "uri-reference": factory.uri,
        "hostname": factory.hostname,
        "ipv4": factory.ipv4,
        "ipv6": factory.ipv6,
        "byte": factory.byte,
        "binary": factory.byte,
        "password": factory.password,
        "duration": lambda: (
            "P" + str(factory.integer(1, 9)) + "DT" + str(factory.integer(1, 23)) + "H"
        ),
    }
    return mapping.get(name)


def pool_for_property(
    name: str, factory: Factory, context: str = "", max_len: int | None = None
) -> Any:
    """Name-aware realism: a field called city should contain a city.

    The context is the enclosing model or operation name. It disambiguates the
    overloaded word name: a pet name inside a pet schema, a person inside a
    customer schema. The caller keeps the value only when it satisfies the declared
    pattern and length, so realism never wins over validity.
    """
    lowered = name.lower()
    context_lowered = context.lower()
    animal_context = any(
        token in context_lowered for token in ("pet", "animal", "dog", "cat", "toy")
    )
    person_context = any(
        token in context_lowered for token in ("customer", "user", "person", "owner", "contact")
    )
    short_country = lower_is_country(lowered) and max_len == 2
    rules: tuple[tuple[tuple[str, ...], Callable[[], Any]], ...] = (
        (("email", "mail"), factory.email),
        (("firstname", "first_name", "givenname"), lambda: factory.person()["first"]),
        (("lastname", "last_name", "surname", "familyname"), lambda: factory.person()["last"]),
        (("company", "organisation", "organization", "vendor", "brand"), factory.company),
        (("city", "town"), factory.city),
        (("street", "address", "line1"), factory.street),
        (("postal", "zip"), factory.postal_code),
        (("sku", "product_code", "article"), factory.sku),
        (("product", "item"), factory.product_name),
        (("color", "colour"), lambda: factory.pick(COLORS)),
        (("currency",), lambda: factory.pick(CURRENCIES)),
        (("tag", "label", "category"), lambda: factory.pick(TAG_WORDS)),
        (("phone", "tel", "mobile"), factory.phone),
        (("url", "link", "href", "website"), factory.uri),
        (("description", "bio", "summary", "note", "caption"), lambda: factory.sentence()),
        (("id", "uuid", "guid"), factory.uuid),
        (("slug",), lambda: factory.word() + "-" + factory.word()),
        (("version",), lambda: str(factory.integer(1, 4)) + "." + str(factory.integer(0, 9))),
    )
    if short_country:
        return factory.country_code()
    for needles, produce in rules:
        if any(needle in lowered for needle in needles):
            return produce()
    if lowered in ("name", "title"):
        if animal_context:
            return factory.pet_name()
        if person_context:
            return factory.full_name()
        return factory.full_name()
    return None


def lower_is_country(lowered: str) -> bool:
    return "country" in lowered or "nation" in lowered
