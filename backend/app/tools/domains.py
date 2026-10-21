"""Source tier classification and domain inspection utilities.

Classifies domains into:
- official: Government, accredited universities, and primary scholarship bodies (.edu, .gov, .ac.uk, etc.)
- reputable: Established international education and scholarship aggregators
- unknown: General web domains, blogs, or untiered platforms

Zero hardcoded scholarship data. Only structural rules and recognizable education domains.
"""

import re
from typing import Optional
from urllib.parse import urlparse
from app.schemas import SourceTier


# Structural top-level and second-level domain patterns for accredited academic & government bodies
OFFICIAL_TLD_PATTERNS = [
    re.compile(r"\.edu(\.[a-z]{2})?$", re.IGNORECASE),
    re.compile(r"\.gov(\.[a-z]{2})?$", re.IGNORECASE),
    re.compile(r"\.ac\.[a-z]{2}$", re.IGNORECASE),
    re.compile(r"\.gob\.[a-z]{2}$", re.IGNORECASE),
    re.compile(r"\.gc\.ca$", re.IGNORECASE),
    re.compile(r"\.uni-[a-z0-9-]+\.de$", re.IGNORECASE),
    re.compile(r"\.tu-[a-z0-9-]+\.de$", re.IGNORECASE),
]

# Explicit official scholarship commissions, foundations, and bilateral state agencies
OFFICIAL_ORGANIZATION_DOMAINS = {
    # Germany / Europe
    "daad.de",
    "daad-germany.in",
    "daad-kenya.org",
    "erasmus-plus.ec.europa.eu",
    "europa.eu",
    "campusfrance.org",
    "study-in-germany.de",
    "studyinsweden.se",
    "studyinholland.nl",
    # USA / Canada / Americas
    "fulbrightprogram.org",
    "cies.org",
    "state.gov",
    "ed.gov",
    "vanier.gc.ca",
    "banting.fellowships-bourses.gc.ca",
    "educanada.ca",
    "nserc-crsng.gc.ca",
    "sshrc-crsh.gc.ca",
    # UK / Commonwealth
    "chevening.org",
    "commonwealthscholarships.org",
    "cscuk.fcdo.gov.uk",
    "britishcouncil.org",
    "rhodeshouse.ox.ac.uk",
    "gatescambridge.org",
    # Singapore / Asia
    "a-star.edu.sg",
    "moe.gov.sg",
    "mfa.gov.sg",
    "singaporetech.edu.sg",
    # Australia / NZ
    "australiaawards.gov.au",
    "nzscholarships.govt.nz",
}

# Reputable aggregators, directories, and verified education media
REPUTABLE_AGGREGATOR_DOMAINS = {
    "scholarshipportal.com",
    "mastersportal.com",
    "bachelorsportal.com",
    "phdportal.com",
    "findamasters.com",
    "findaphd.com",
    "topuniversities.com",  # QS
    "timeshighereducation.com",
    "usnews.com",
    "wemakescholars.com",
    "buddy4study.com",
    "iefa.org",
    "internationalscholarships.com",
    "scholars4dev.com",
    "opportunitiesforafricans.com",
    "youthop.com",
    "advance-africa.com",
}

# Blocklist for social networks, general video sites, forums, and link farms
IRRELEVANT_DOMAINS_BLOCKLIST = {
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "quora.com",
    "youtube.com",
    "vimeo.com",
    "medium.com",
    "linkedin.com",  # Handled separately via student profile, not search candidates
}


def extract_host(url: str) -> str:
    """Extract and normalize host from a URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        netloc = parsed.netloc.lower()
        # Strip port and www. prefix if present
        host = netloc.split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def registrable_domain(url: str) -> str:
    """Extract registrable root domain (e.g. cs.toronto.edu -> toronto.edu)."""
    host = extract_host(url)
    if not host:
        return ""

    parts = host.split(".")
    if len(parts) <= 2:
        return host

    # Handle multi-part suffixes like .co.uk, .ac.uk, .edu.sg, .gov.in, .gc.ca
    two_part_suffixes = {
        "co.uk", "ac.uk", "gov.uk", "edu.sg", "gov.sg", "gov.in", "edu.in",
        "ac.in", "gc.ca", "edu.au", "gov.au", "ac.nz", "govt.nz", "edu.cn"
    }
    last_two = ".".join(parts[-2:])
    if last_two in two_part_suffixes and len(parts) >= 3:
        return ".".join(parts[-3:])

    return ".".join(parts[-2:])


def is_official_domain(url: str) -> bool:
    """Check if domain meets official government, university, or scholarship body criteria."""
    host = extract_host(url)
    if not host:
        return False

    reg_domain = registrable_domain(url)

    # Check explicit allowlist
    if host in OFFICIAL_ORGANIZATION_DOMAINS or reg_domain in OFFICIAL_ORGANIZATION_DOMAINS:
        return True

    # Check structural TLD patterns (.edu, .gov, .ac.xx, .gc.ca, etc.)
    for pattern in OFFICIAL_TLD_PATTERNS:
        if pattern.search(host):
            return True

    return False


def is_reputable_domain(url: str) -> bool:
    """Check if domain is a known reputable international aggregator."""
    host = extract_host(url)
    reg_domain = registrable_domain(url)
    return host in REPUTABLE_AGGREGATOR_DOMAINS or reg_domain in REPUTABLE_AGGREGATOR_DOMAINS


def is_blocked_domain(url: str) -> bool:
    """Check if domain is an irrelevant social or community forum."""
    host = extract_host(url)
    reg_domain = registrable_domain(url)
    return host in IRRELEVANT_DOMAINS_BLOCKLIST or reg_domain in IRRELEVANT_DOMAINS_BLOCKLIST


def classify_source_tier(url: str) -> SourceTier:
    """Return 'official', 'reputable', or 'unknown' for a given URL."""
    if not url:
        return "unknown"
    if is_official_domain(url):
        return "official"
    if is_reputable_domain(url):
        return "reputable"
    return "unknown"
