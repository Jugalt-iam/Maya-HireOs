#!/usr/bin/env python3
"""
make-demo-archive.py  --  builds the demo archive for the public instance.

    python3 make-demo-archive.py /opt/maya-os/MyData/conversations.json

Writes a Claude-export-shaped conversations.json full of invented but realistic
marketing work, so the demo can show recall, routing and retrieval doing real
things without a single line of anyone's private history leaving their machine.

Everything in here is fiction. The companies do not exist.
"""

import json
import sys
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 6, 12, 9, 30, tzinfo=timezone.utc)


def ts(days: int, hours: int = 0) -> str:
    return (BASE + timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def convo(uuid, name, day, turns):
    msgs = []
    for i, (sender, text) in enumerate(turns):
        msgs.append({
            "uuid": "%s-m%d" % (uuid, i),
            "text": text,
            "sender": sender,
            "created_at": ts(day, i),
            "updated_at": ts(day, i),
            "attachments": [], "files": [],
        })
    return {"uuid": uuid, "name": name, "created_at": ts(day),
            "updated_at": ts(day, len(turns)), "chat_messages": msgs}


DEMO = [
    convo("c01", "Northwind Rail launch, three angles", 0, [
        ("human",
         "We are launching Northwind Rail's freight tracking product to logistics "
         "managers in the UK. Mid market, 50 to 500 trucks. They currently use "
         "spreadsheets and phone calls. Give me three campaign angles that are "
         "genuinely different, not three phrasings of the same idea."),
        ("assistant",
         "Three angles, each on a different mechanism and a different awareness "
         "level.\n\nAngle one, Problem Aware, the cost of not knowing. Lead with "
         "the phone call a manager makes when a customer asks where the load is "
         "and nobody can answer. Mechanism: live position rather than last "
         "reported checkpoint. Framework PAS.\n\nAngle two, Solution Aware, "
         "against the spreadsheet not against competitors. Most of this market "
         "does not know software exists for this, they think the alternative is "
         "a better spreadsheet. Mechanism: the spreadsheet cannot call you when "
         "something goes wrong. Framework Before-After-Bridge.\n\nAngle three, "
         "Most Aware, the switching cost objection. They have tried something "
         "and abandoned it. Mechanism: no hardware install, works off the "
         "driver's existing phone. Framework Hook-Story-Offer.\n\nTest all three "
         "at equal spend for six days. Kill anything under 0.9 percent CTR on "
         "day four."),
    ]),
    convo("c02", "Ad teardown, Kestrel Coffee subscription", 3, [
        ("human",
         "Tore down the Kestrel Coffee creative today. Hook read 'Great coffee, "
         "delivered'. Scores were Clarity 8, Hook 3, Offer 5, Thumb-stop 4."),
        ("assistant",
         "Hook at 3 is the whole problem and the other three barely matter until "
         "it moves. 'Great coffee, delivered' is a category statement, any "
         "roaster in the country could run it unchanged, which is the definition "
         "of a 3 on that axis.\n\nThe offer at 5 is a discount with a deadline "
         "and no reason for the deadline, so it reads as arbitrary and people "
         "discount arbitrary deadlines.\n\nBiggest point of leverage is the "
         "hook. Three replacements: the mechanism angle, roasted the morning it "
         "ships, which nobody else can claim without a small roastery. The "
         "direct callout, your coffee was roasted in March. And the contrarian, "
         "subscription coffee is usually stale, here is why ours is not."),
    ]),
    convo("c03", "Brand voice notes, what actually works", 7, [
        ("human",
         "Reviewing the last quarter of copy. The lines that performed were "
         "short, specific and slightly blunt. The ones that died were the ones "
         "that sounded like a brand rather than a person."),
        ("assistant",
         "That pattern holds across everything in the archive. The winners share "
         "three things: a concrete number in the first line, an objection named "
         "out loud rather than avoided, and sentences under twelve words in the "
         "opening. The losers share one thing, they could have been written by "
         "any company in the category.\n\nThe rule that falls out of it: if the "
         "line would survive being pasted onto a competitor's site unchanged, it "
         "is not doing any work."),
    ]),
    convo("c04", "Harbourline invoice chase, tone", 12, [
        ("human",
         "Harbourline are 40 days late on invoice 2291, about 4.2 lakh. They "
         "have always paid before, this is the first time. What tone?"),
        ("assistant",
         "Warm, not firm. A good payer late once is almost always a process "
         "problem on their side rather than a decision, and a firm letter to a "
         "reliable client costs you more than the 40 days does.\n\nReference the "
         "invoice number and the amount so it can be actioned without a search, "
         "give a specific date rather than 'at your earliest convenience', and "
         "offer the out: if it is stuck in approvals tell me who to chase and I "
         "will chase them. Escalate only if it passes 60 days, and then it "
         "changes to firm with a dated work-stop."),
    ]),
    convo("c05", "Vedic chart reading notes", 18, [
        ("human",
         "Been reading about kundli and vedic chart construction, the twelve "
         "houses and how the ascendant is calculated from birth time and place."),
        ("assistant",
         "The core of it is that the ascendant, the lagna, is the sign rising on "
         "the eastern horizon at the moment of birth, which is why birth time "
         "matters to the minute and birth place matters at all. It shifts about "
         "one degree every four minutes.\n\nThe twelve houses are counted from "
         "there, and each one governs an area of life. The chart is the same sky "
         "for everyone born that day, the houses are what make it personal."),
    ]),
    convo("c06", "Meridian Health, why the funnel leaks", 24, [
        ("human",
         "Meridian Health get 2,400 landing page visits a month and 31 demo "
         "bookings. Where is the leak?"),
        ("assistant",
         "1.3 percent is not a leak, it is the normal rate for a page asking for "
         "a demo from a cold visitor. The question is whether a demo is the "
         "right ask at that stage.\n\nAt 2,400 visits, an intermediate step that "
         "converts at 8 to 12 percent gives you 200 to 290 people you can talk "
         "to instead of 31. The demo request stays for the ones who are ready.\n\n"
         "Before changing anything, check how many of the 31 actually show up. "
         "If show rate is under 60 percent the problem is qualification, not "
         "volume, and more leads makes it worse."),
    ]),
    convo("c07", "Last task, Q3 creative refresh brief", 33, [
        ("human",
         "Starting the Q3 creative refresh. Need the brief by Friday: what we "
         "keep, what we kill, and the three new angles to test."),
        ("assistant",
         "Keep the mechanism angle, it carried the quarter and the fatigue curve "
         "has not turned yet. Kill the discount creative, it trained the list to "
         "wait for a sale and the margin never recovered.\n\nThree to test: the "
         "objection-first open, the customer's own words as the headline, and "
         "the anti-claim, where you name what the product does not do. That last "
         "one has the highest variance and the highest ceiling.\n\nBrief should "
         "carry the kill rule on the front page, not the back. Everything gets "
         "six days and 0.9 percent CTR or it goes."),
    ]),
    convo("c08", "Pricing, whether to raise", 38, [
        ("human",
         "Thinking about raising retainers 15 percent in October. Nervous about "
         "churn."),
        ("assistant",
         "The number to look at is not churn risk, it is what proportion of "
         "current clients would leave over 15 percent and what they contribute. "
         "If the bottom third of accounts by margin are also the most price "
         "sensitive, losing some of them raises your average margin twice, once "
         "on price and once on mix.\n\nThe honest risk is the middle: good "
         "clients on old pricing who feel the increase is arbitrary. That is a "
         "communication problem, not a pricing one. Give them the reason and "
         "sixty days notice and most of it disappears."),
    ]),
]


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "conversations.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(DEMO, fh, ensure_ascii=False, indent=1)
    turns = sum(len(c["chat_messages"]) for c in DEMO)
    print("wrote %d conversations, %d messages -> %s" % (len(DEMO), turns, out))
    print("all fiction. no real client, number or person appears in it.")


if __name__ == "__main__":
    main()
