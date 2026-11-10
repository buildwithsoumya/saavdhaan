"""Public demo mode: anyone can open the app, so usage is capped per day instead of gated by a token.

Each browser or app sends its own random visitor id; family voices belong to that visitor.
"""
import re
import time
from collections import Counter

VISITOR = re.compile(r"[A-Za-z0-9-]{8,64}")

LIMIT = "आज की डेमो सीमा पूरी हो गई, कल फिर कोशिश करें · Demo limit reached for today, please try tomorrow"
BUSY = "अभी बहुत लोग डेमो इस्तेमाल कर रहे हैं, कुछ मिनट बाद कोशिश करें · The demo is busy, please try again in a few minutes"
ONE_CALL = "इस डिवाइस पर एक कॉल पहले से सुनी जा रही है · This device is already listening to a call"
TOO_LONG = "डेमो कॉल {minutes} मिनट तक सीमित है · Demo calls stop after {minutes} minutes"


class Budget:
    """Daily usage counters, reset when the UTC day changes.
    ponytail: in memory, so a restart resets them; move to a store if limits must survive restarts."""

    def __init__(self, today=lambda: time.strftime("%Y-%m-%d", time.gmtime())):
        self.today, self.day, self.counts = today, None, Counter()

    def _roll(self):
        if self.today() != self.day:
            self.day, self.counts = self.today(), Counter()

    def used(self, key: str) -> float:
        self._roll()
        return self.counts[key]

    def add(self, key: str, n: float = 1) -> None:
        self._roll()
        self.counts[key] += n

    def take(self, key: str, limit: float, n: float = 1) -> bool:
        """Count n uses if that stays within the limit."""
        if self.used(key) + n > limit:
            return False
        self.counts[key] += n
        return True
