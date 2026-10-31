"""Deterministic synthetic identities.

Two disjoint name pools:

* SOURCE names play the role of the real PII that must be removed.
* TARGET names form the pseudonym pool the cleaner must draw replacements from.

All domains are reserved for documentation/testing (RFC 2606 / RFC 6761), so generated contact
details cannot belong to anyone.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

SOURCE_FIRST = """
Aurelia Bartosz Chiara Dmitri Esperanza Farhan Greta Haruki Ingrid Jovan Kalinda Leopold
Mireille Nikolai Oksana Priyanka Quentin Rosalind Soren Tatiana Umberto Valentina Wendell
Ximena Yusuf Zofia Anselm Brigitte Cosimo Dagny Emeric Fenella Gideon Hortense Isidore
Jessamy Kasimir Ludmila Marisol Ottilie Pascual Radomir Seraphina Thaddeus Ulrike Vasco
Wilhelmina Yolanda Zoltan Amara Bastian Clementine Desmond Eulalia Florian Giselle Horatio Imelda
""".split()

SOURCE_LAST = """
Achterberg Barzani Castellanos Dobrowolski Eriksen Gallagher Halvorsen Ibarra Jablonski
Kowalczyk Lindqvist Mendoza Nakashima Oyelaran Petrakis Quiroga Rasmussen Szymanski Takahashi
Urquhart Vasquez Wojcik Xiong Yamamoto Zielinski Arcuri Brennan Coutinho Delacroix Eberhardt
Ferreira Grimaldi Hakimi Iwasaki Jorgensen Kaminski Lombardi Moreau Ostrowski Pellegrini Rautio
Sandoval Thorvaldsen Ulyanov Vermeulen Wainwright Yilmaz Zamora Bergstrom Castellano
""".split()

TARGET_FIRST = """
Adaeze Bjorn Calliope Dariusz Elowen Fabian Galina Henrik Isolde Jarek Kirsi Lucian Margit
Orla Piotr Rhiannon Stellan Thea Ugo Vesna Wiebke Yannick Zuzana Ambrose Beatrix Caspian
Delphine Evander Freya Gunnar Ignatius Katarina Lysander Minerva Nolwenn Osric Perpetua Rufus
Saoirse Tobias Viggo Winifred Yara Zenobia Anouk Benedikt Cressida Dorian Esme Fintan Greer
Hamish Ilse Leontine Matthias
""".split()

TARGET_LAST = """
Ashdown Blomqvist Carrington Duquesne Ellingsen Fitzroy Gustafsson Hallorann Isaksen Jankowski
Kerrigan Laurentis Marchetti Nordqvist Penhaligon Quennell Rothwell Sundqvist Tremblay Uccello
Valdivia Westergaard Yardley Zabrowski Abernethy Bergkamp Cavendish Dunmore Engstrom Galbraith
Holmquist Ingersoll Kjeldsen Lindgren Montague Northcott Okonkwo Pemberton Ravenscroft
Strandberg Thistlethwaite Underhill Whitmore Ysselstein Zeller Albrecht Brightwater Corcoran
""".split()

# Overflow names for pools larger than the lists above (tasks with more than about 30 people). They are used only
# for the extra personas, so smaller pools, and every task generated before they existed, are unchanged.
EXTRA_TARGET_FIRST = """
Aldous Brisa Cormac Dervla Eamon Fiadh Gwilym Hedda Iolo Jutta Kester Liesl Malin Nesta Quorra Ruairi Sigrun
Teodor Vidar Ximo Ysolde Zelig
""".split()
EXTRA_TARGET_LAST = """
Aldridge Bramwell Coverdale Dewhurst Everleigh Fairbairn Greenhalgh Hollingworth Inchbald Jessop Kinsella Lockhart
Mallory Nettleford Ormerod Quarrie Rushworth Sowerby Tunstall
""".split()

SOURCE_ORG_DOMAINS = ["example.com", "corp.example.com", "mail.example.org", "dev.example.net", "lab.example.org"]
SOURCE_PERSONAL_DOMAINS = ["example.net", "inbox.example.com", "post.example.org"]
TARGET_DOMAIN = "pseudonym.example"

assert not ({n.lower() for n in SOURCE_FIRST + SOURCE_LAST}
            & {n.lower() for n in TARGET_FIRST + TARGET_LAST + EXTRA_TARGET_FIRST + EXTRA_TARGET_LAST})
assert not (set(EXTRA_TARGET_FIRST) & set(TARGET_FIRST)) and not (set(EXTRA_TARGET_LAST) & set(TARGET_LAST))
assert len(set(SOURCE_FIRST)) == len(SOURCE_FIRST) and len(set(SOURCE_LAST)) == len(SOURCE_LAST)

# Informal short forms used in casual comments; entries that are English words are skipped at generation.
NICKNAMES = {
    "Aurelia": "Aury", "Bartosz": "Bartek", "Chiara": "Chiarina", "Dmitri": "Dima", "Esperanza": "Espe",
    "Farhan": "Farhi", "Greta": "Gretl", "Haruki": "Haru", "Ingrid": "Inga", "Jovan": "Jovo", "Kalinda": "Kali",
    "Leopold": "Poldi", "Mireille": "Mimi", "Nikolai": "Kolya", "Oksana": "Ksyusha", "Priyanka": "Pri",
    "Quentin": "Quint", "Rosalind": "Roz", "Soren": "Sorry", "Tatiana": "Tanya", "Umberto": "Berto",
    "Valentina": "Vale", "Wendell": "Dell", "Ximena": "Xime", "Yusuf": "Yusi", "Zofia": "Zosia", "Anselm": "Anse",
    "Brigitte": "Gitte", "Cosimo": "Cosi", "Dagny": "Dagi", "Emeric": "Emi", "Fenella": "Nella", "Gideon": "Gid",
    "Hortense": "Tensie", "Isidore": "Izzy", "Jessamy": "Jess", "Kasimir": "Kasi", "Ludmila": "Lida",
    "Marisol": "Mari", "Ottilie": "Tilly", "Pascual": "Pasco", "Radomir": "Radek", "Seraphina": "Sera",
    "Thaddeus": "Thad", "Ulrike": "Ulli", "Vasco": "Vasquinho", "Wilhelmina": "Mina", "Yolanda": "Yoli",
    "Zoltan": "Zoli", "Amara": "Ami", "Bastian": "Basti", "Clementine": "Clem", "Desmond": "Dez",
    "Eulalia": "Lalia", "Florian": "Flo", "Giselle": "Gigi", "Horatio": "Raitch", "Imelda": "Mel",
}

NAME_FORMS = {"full_name", "first_name", "last_name", "initial_last", "last_first", "nickname"}
FORM_CLASS = {
    "full_name": "name",
    "first_name": "name",
    "last_name": "name",
    "initial_last": "name",
    "last_first": "name",
    "nickname": "name",
    "email": "email",
    "email_obfuscated": "email",
    "username": "username",
}


def obfuscate_email(email: str) -> str:
    """Jira-style mail-archive obfuscation: ``a.b@x.org`` -> ``a.b at x dot org``."""
    local, domain = email.split("@", 1)
    return f"{local} at {domain.replace('.', ' dot ')}"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class SourceIdentity:
    """A synthetic person standing in for one real developer (the PII to remove)."""

    first: str
    last: str
    org_domain: str
    personal_domain: str
    digits: str
    usernames: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def initial_last(self) -> str:
        return f"{self.first[0]}. {self.last}"

    @property
    def last_first(self) -> str:
        return f"{self.last}, {self.first}"

    @property
    def nickname(self) -> str:
        return NICKNAMES.get(self.first, "")

    @property
    def work_email(self) -> str:
        return f"{slug(self.first)}.{slug(self.last)}@{self.org_domain}"

    @property
    def personal_email(self) -> str:
        return f"{slug(self.first)}{slug(self.last)[0]}{self.digits}@{self.personal_domain}"

    def project_email(self, project_domain: str) -> str:
        return f"{self.usernames[0]}@{project_domain}"

    def noreply_email(self) -> str:
        return f"{self.usernames[0]}@users.noreply.example.com"

    def sensitive_tokens(self) -> dict[str, list[str]]:
        return {"names": [self.first, self.last], "usernames": list(self.usernames)}


@dataclass
class Persona:
    """A target pseudonym from the pool."""

    persona_id: str
    first: str
    last: str

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def initial_last(self) -> str:
        return f"{self.first[0]}. {self.last}"

    @property
    def email(self) -> str:
        return f"{slug(self.first)}.{slug(self.last)}@{TARGET_DOMAIN}"

    @property
    def username(self) -> str:
        return f"{slug(self.first)[0]}{slug(self.last)}"

    def renderings(self) -> dict[str, str]:
        return {
            "full_name": self.full_name,
            "first_name": self.first,
            "last_name": self.last,
            "initial_last": self.initial_last,
            "email": self.email,
            "username": self.username,
        }

    def to_json(self) -> dict:
        return {"persona_id": self.persona_id, **{k: v for k, v in self.renderings().items() if k != "initial_last"}}


def render_persona(p: dict, form: str) -> str:
    """Render a pool persona (JSON form) in the format of an occurrence's form."""
    first, last = p["first_name"], p["last_name"]
    return {
        "full_name": p["full_name"],
        "first_name": first,
        "nickname": first,
        "last_name": last,
        "initial_last": f"{first[0]}. {last}",
        "last_first": f"{last}, {first}",
        "email": p["email"],
        "email_obfuscated": obfuscate_email(p["email"]),
        "username": p["username"],
    }[form]


def _username_candidates(first: str, last: str, rng: random.Random) -> list[str]:
    f, l = slug(first), slug(last)
    cands = [f"{f[0]}{l}", f"{f}{l[0]}", f"{f}.{l}", f"{f}-{l[:3]}", f"{l}{f[0]}", f"{f[:4]}{l[:2]}"]
    rng.shuffle(cands)
    return cands


class IdentityFactory:
    """Draws unique source identities and target personas from a seeded RNG."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self._used_src: set[tuple[str, str]] = set()
        self._used_first: set[str] = set()
        self._used_last: set[str] = set()
        self._used_usernames: set[str] = set()
        self._used_tgt: set[tuple[str, str]] = set()

    def source_identity(self, n_usernames: int = 2, first: str | None = None, last: str | None = None,
                        exclude_domains: set[str] | None = None) -> SourceIdentity:
        """Name tokens are unique within a factory unless ``first``/``last`` force a deliberate collision."""
        forced = first is not None and last is not None
        for _ in range(5000):
            f = first or self.rng.choice(SOURCE_FIRST)
            l = last or self.rng.choice(SOURCE_LAST)
            if (f, l) in self._used_src and not forced:
                continue
            if (first is None and f in self._used_first) or (last is None and l in self._used_last):
                continue
            break
        else:  # pragma: no cover
            raise RuntimeError("source name pool exhausted")
        self._used_src.add((f, l))
        self._used_first.add(f)
        self._used_last.add(l)
        usernames = []
        for cand in _username_candidates(f, l, self.rng):
            if cand not in self._used_usernames:
                usernames.append(cand)
                self._used_usernames.add(cand)
            if len(usernames) == n_usernames:
                break
        org = [d for d in SOURCE_ORG_DOMAINS if d not in (exclude_domains or set())]
        personal = [d for d in SOURCE_PERSONAL_DOMAINS if d not in (exclude_domains or set())]
        return SourceIdentity(
            first=f,
            last=l,
            org_domain=self.rng.choice(org),
            personal_domain=self.rng.choice(personal),
            digits=str(self.rng.randint(10, 99)),
            usernames=usernames,
        )

    def reserve_username(self, name: str) -> str:
        base, k = name, 2
        while name in self._used_usernames:
            name = f"{base}{k}"
            k += 1
        self._used_usernames.add(name)
        return name

    def persona_pool(self, n: int) -> list[Persona]:
        pool = []
        firsts = list(TARGET_FIRST)
        self.rng.shuffle(firsts)
        firsts += EXTRA_TARGET_FIRST  # only reached when n exceeds the base list
        lasts = TARGET_LAST if n <= len(TARGET_LAST) else TARGET_LAST + EXTRA_TARGET_LAST
        if n > min(len(firsts), len(lasts)):
            raise ValueError(f"pool of {n} personas exceeds the name lists")
        for i in range(n):
            f = firsts[i]
            for _ in range(1000):
                l = self.rng.choice(lasts)
                if (f, l) not in self._used_tgt and all(p.last != l for p in pool) \
                        and all(p.username != f"{slug(f)[0]}{slug(l)}" for p in pool):
                    break
            self._used_tgt.add((f, l))
            pool.append(Persona(persona_id=f"P{i + 1:02d}", first=f, last=l))
        return pool


def pool_size(n_entities: int) -> int:
    """Pool is larger than the entity count so its size does not reveal it."""
    return max(n_entities + 2, -(-n_entities * 3 // 2) + 2)
