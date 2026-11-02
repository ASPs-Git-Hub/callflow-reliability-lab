"""Match an inbound reply to an appointment: exact reference, then phone plus date, then fuzzy name plus date.
Ambiguous or weak matches are never auto-accepted: they go to a person."""
from difflib import SequenceMatcher

from .model import norm_phone

FUZZY_MIN = 0.85


def match_reply(reply, appointments):
    """Returns (status, appointment_or_candidates, how). status: matched, ambiguous or unmatched."""
    ref = (reply.get("ref") or "").strip().upper()
    if ref:
        hits = [a for a in appointments if a["id"].upper() == ref]
        if len(hits) == 1:
            return "matched", hits[0], "reference"
        if len(hits) > 1:
            return "ambiguous", hits, "reference"
    phone = norm_phone(reply.get("phone", ""))
    if phone and reply.get("date"):
        hits = [a for a in appointments if norm_phone(a["phone"]) == phone and a["date"] == reply["date"]]
        if len(hits) == 1:
            return "matched", hits[0], "phone+date"
        if len(hits) > 1:
            return "ambiguous", hits, "phone+date"
    name = (reply.get("name") or "").strip().lower()
    if name and reply.get("date"):
        hits = [a for a in appointments if a["date"] == reply["date"]
                and SequenceMatcher(None, name, a["name"].lower()).ratio() >= FUZZY_MIN]
        if len(hits) == 1:
            return "matched", hits[0], "name+date"
        if len(hits) > 1:
            return "ambiguous", hits, "name+date"
    return "unmatched", [], "none"
