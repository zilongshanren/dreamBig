"""Review scrapers for downstream NLP (sentiment + topic clustering).

Each platform's adapter inherits BaseReviewScraper and returns
normalized ReviewEntry objects (0-5 rating scale, UTF-8 content,
datetime timestamps).
"""

from .app_store import AppStoreReviewScraper
from .base import BaseReviewScraper, ReviewEntry
from .google_play import GooglePlayReviewScraper
from .h5_4399 import H5_4399ReviewScraper
from .steam import SteamReviewScraper
from .taptap import TapTapReviewScraper

__all__ = [
    "ReviewEntry",
    "BaseReviewScraper",
    "GooglePlayReviewScraper",
    "SteamReviewScraper",
    "AppStoreReviewScraper",
    "TapTapReviewScraper",
    "H5_4399ReviewScraper",
]
