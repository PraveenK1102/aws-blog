#!/usr/bin/env python
"""Seed the DEV environment ONLY (LocalStack) with 5 users + weighted-interest
blogs, created through the REAL createpost handler (writes S3+DDB+SQS in
LocalStack; the dev_worker then ingests them). Production stays clean.

Run the ask server (local/run_local.sh) and the worker (local/dev_worker.py)
first, then:  .venv/bin/python local/dev_seed.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
LAMBDAS = os.path.join(HERE, "..", "lambdas")
SERVER = "http://localhost:8080"


def _load_env(path):
    if not os.path.exists(path):
        return
    for raw in open(path):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


_load_env(os.path.join(HERE, ".env"))
sys.path.insert(0, os.path.join(LAMBDAS, "create_post"))
sys.path.insert(0, LAMBDAS)
import handler as cp  # create_post/handler.py


def _post(path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def signup_or_login(email, password, display_name, domain):
    try:
        return _post("/api/auth/signup", {"email": email, "password": password,
                                          "display_name": display_name, "domain": domain})
    except urllib.error.HTTPError as e:
        if e.code == 409:  # already exists → login
            return _post("/api/auth/login", {"email": email, "password": password})
        raise


def create_post(token, title, content):
    event = {"headers": {"authorization": f"Bearer {token}"},
             "body": json.dumps({"title": title, "content": content})}
    resp = cp.handler(event, None)
    return json.loads(resp["body"])


# ---------------------------------------------------------------------------
# 5 users (real Tamil names). email = <username>@gmail.com,
# password = <username>@password@123. Blogs weighted to each user's (high) topic.
# ---------------------------------------------------------------------------
USERS = [
    {
        "name": "Karthik Raja", "username": "karthikraja", "domain": "fitness",
        "posts": [
            ("My 16-Week Marathon Training Plan",
             "# My 16-Week Marathon Training Plan\n\n## Base Building\n\nThe first six weeks are all about aerobic base. I run five days a week, keeping most runs at an easy conversational pace. Weekly mileage climbs gradually from 40 to 65 kilometres, never increasing by more than ten percent week over week to avoid injury.\n\n## Speed and Tempo\n\nFrom week seven I add one tempo run and one interval session. Tempo runs are 8 to 10 kilometres at threshold pace; intervals are 6x800 metres with short recoveries. These teach the body to clear lactate and hold a hard effort.\n\n## Taper\n\nThe final three weeks cut volume by half while keeping intensity, so the legs feel fresh on race day. Sleep and hydration matter more than any workout during the taper."),
            ("Progressive Overload: The Only Rule That Matters",
             "# Progressive Overload\n\n## The Core Idea\n\nMuscles adapt to the stress you place on them. To keep getting stronger you must gradually increase that stress, whether by adding weight, reps, or sets. This is progressive overload, and it is the single most important principle in strength training.\n\n## How I Apply It\n\nI keep a training log and try to beat the previous session by a small margin, usually one extra rep or 2.5 kilograms. Over months these tiny gains compound into large strength increases on squats, deadlifts, and presses.\n\n## Recovery\n\nOverload only works if you recover. I take at least one full rest day and prioritise protein and sleep. Progress stalls when recovery is ignored."),
            ("Why Zone 2 Cardio Changed My Endurance",
             "# Zone 2 Cardio\n\n## What Is Zone 2\n\nZone 2 is a low-intensity effort where you can still hold a conversation, roughly 60 to 70 percent of maximum heart rate. It builds mitochondrial density and fat-burning efficiency.\n\n## My Results\n\nAfter three months of mostly Zone 2 running my resting heart rate dropped and my race pace at the same heart rate improved noticeably. Slowing down actually made me faster over long distances.\n\n## The Discipline\n\nThe hardest part is holding back the ego and running slow. A heart-rate monitor keeps me honest."),
            ("Reliving the Chennai Super Kings IPL Run",
             "# Chennai Super Kings\n\n## A Season to Remember\n\nAs a lifelong cricket fan, watching CSK lift the trophy was special. The captaincy was calm under pressure and the bowling in the death overs was clinical.\n\n## Key Moments\n\nThe spin trio choked the middle overs on slow Chepauk pitches, and the finishing with the bat in the last two overs won several close games. Home advantage was huge.\n\n## Fan Culture\n\nThe whole city turns yellow during the season. Sport at its best brings people together."),
        ],
    },
    {
        "name": "Anitha Rani", "username": "anitharani", "domain": "gymnastics",
        "posts": [
            ("Finding Balance on the Beam",
             "# Balance on the Beam\n\n## The Fundamentals\n\nThe balance beam is only ten centimetres wide, so every movement must be precise. I train barefoot to feel the surface and keep my gaze fixed on a point at the end of the beam rather than looking down.\n\n## Drills\n\nI practise walking, releve holds, and slow leg swings on a floor line first, then a low beam, and finally the competition beam. Confidence transfers upward as the height increases.\n\n## Mental Game\n\nStaying calm is half the skill. I breathe slowly and commit fully to each element, because hesitation is what causes falls."),
            ("Conditioning Drills Every Gymnast Needs",
             "# Conditioning for Gymnasts\n\n## Core Strength\n\nHollow-body holds, arch holds, and leg lifts build the trunk stability that every skill depends on. I do these daily, aiming for clean form over long durations.\n\n## Upper Body\n\nHandstand holds against a wall, pull-ups, and press-to-handstand progressions develop the pressing and pulling strength needed for bars and vault.\n\n## Flexibility\n\nSplits, bridges, and shoulder openers keep the range of motion required for clean lines. Conditioning is unglamorous but it is the foundation of everything."),
            ("How I Finally Landed My Back Handspring",
             "# Back Handspring\n\n## The Fear\n\nGoing backwards blind is terrifying at first. I spent weeks doing back handsprings into a soft pit and with a coach spotting me before trying it alone.\n\n## The Technique\n\nThe key was sitting back as if into a chair, then exploding upward and reaching backward with straight arms. A strong block off the hands finishes the skill.\n\n## Breakthrough\n\nThe day it finally clicked, everything felt slow and controlled. Patience and hundreds of repetitions made the difference."),
            ("Starting My Index Fund Journey",
             "# Index Fund Investing\n\n## Why Index Funds\n\nAfter reading about compounding I opened a low-cost index fund that tracks a broad market. Instead of picking stocks I own a slice of hundreds of companies at once.\n\n## My Approach\n\nI invest a fixed amount every month regardless of market news. This automatic habit removes emotion from the decision.\n\n## Long Horizon\n\nI treat this money as untouchable for at least ten years, letting compounding do the heavy lifting."),
        ],
    },
    {
        "name": "Senthil Kumar", "username": "senthilkumar", "domain": "military",
        "posts": [
            ("Lessons the Army Taught Me About Discipline",
             "# Discipline in the Army\n\n## Small Habits\n\nDiscipline is built from tiny repeated actions: making your bed with tight corners, polishing boots, being early. These habits train the mind to do the hard thing without debate.\n\n## Under Pressure\n\nWhen exhausted and cold, discipline is what makes you complete the task anyway. It is a muscle strengthened through consistent practice.\n\n## Carrying It Forward\n\nYears later these habits still shape how I approach work and family. Discipline is freedom, not restriction."),
            ("A Day in Basic Military Training",
             "# Basic Military Training\n\n## Before Dawn\n\nThe day starts at 5 a.m. with physical training: runs, push-ups, and circuits designed to break you down and build you back stronger.\n\n## Skills and Drills\n\nMornings cover weapon handling, drill movements, and first aid. Attention to detail is drilled relentlessly because in the field mistakes cost lives.\n\n## Teamwork\n\nEvery task is a team task. You quickly learn that the section only succeeds together, and that trust is earned through shared hardship."),
            ("Fieldcraft and Navigation Fundamentals",
             "# Fieldcraft and Navigation\n\n## Reading the Ground\n\nFieldcraft is the art of moving and surviving in the field: using cover, staying quiet, and observing without being seen. The ground itself tells you where to move.\n\n## Map and Compass\n\nBefore relying on any device, a soldier must navigate with map, compass, and pacing. Setting a bearing and counting paces gets you to a point in fog or darkness.\n\n## Concealment\n\nShape, shine, shadow, silhouette, and movement give away a position. Breaking up outline with natural materials keeps you hidden."),
            ("Yoga for Soldiers: Recovery and Focus",
             "# Yoga for Recovery\n\n## Why Yoga\n\nAfter years of heavy rucking my joints needed care. Yoga restored mobility in my hips and spine that strength training alone could not.\n\n## Breath and Focus\n\nControlled breathing during holds calms the nervous system, which is valuable both for recovery and for staying steady under stress.\n\n## Simple Routine\n\nTwenty minutes of sun salutations, hip openers, and a long final relaxation each evening improved my sleep and reduced old aches."),
        ],
    },
    {
        "name": "Divya Bharathi", "username": "divyabharathi", "domain": "technology",
        "posts": [
            ("Building My First Full-Stack React App",
             "# My First Full-Stack App\n\n## The Frontend\n\nI built the interface in React using functional components and hooks. State management started with useState and grew into a small context for the logged-in user.\n\n## The Backend\n\nA simple REST API served JSON, and I learned to think about endpoints, status codes, and validation. Connecting the two taught me how requests actually flow.\n\n## Lessons\n\nThe hardest part was not the code but structuring the project and handling errors gracefully. Shipping something end to end taught me more than any tutorial."),
            ("Understanding Kubernetes Pods and Deployments",
             "# Kubernetes Basics\n\n## Pods\n\nA pod is the smallest deployable unit and usually wraps a single container. Pods are ephemeral; when one dies, Kubernetes replaces it.\n\n## Deployments\n\nA Deployment manages a set of identical pods and handles rolling updates and rollbacks. You declare the desired state and the controller makes reality match it.\n\n## Services\n\nBecause pod IPs change, a Service gives a stable address and load-balances across pods. Understanding this trio made the whole system click for me."),
            ("Hunting Down a Python Memory Leak",
             "# Chasing a Memory Leak\n\n## The Symptom\n\nA long-running worker slowly consumed more RAM until it crashed. Restarts hid the problem but did not solve it.\n\n## The Investigation\n\nI used tracemalloc to snapshot allocations over time and found a cache that never evicted entries. A global dictionary kept growing forever.\n\n## The Fix\n\nSwitching to an LRU cache with a size limit fixed it. The lesson: unbounded caches are memory leaks waiting to happen."),
            ("My Grandmother's Chettinad Chicken Recipe",
             "# Chettinad Chicken\n\n## The Masala\n\nMy grandmother roasts fennel, peppercorns, dried red chillies, and coconut, then grinds them into a fragrant Chettinad masala. The freshly roasted spices are what make the dish.\n\n## Cooking\n\nOnions and tomatoes are sauteed until soft, the chicken is browned, and the ground masala simmers everything together until the oil separates.\n\n## Serving\n\nFinished with curry leaves and served with steamed rice or dosa, it tastes of home. Some family recipes cannot be improved, only followed."),
        ],
    },
    {
        "name": "Bala Murugan", "username": "balamurugan", "domain": "finance",
        "posts": [
            ("Dollar-Cost Averaging vs Timing the Market",
             "# Dollar-Cost Averaging\n\n## The Strategy\n\nDollar-cost averaging means investing a fixed sum at regular intervals regardless of price. You buy more units when prices are low and fewer when high.\n\n## Why It Works\n\nIt removes the impossible task of timing the market and protects against investing everything right before a fall. Consistency beats prediction.\n\n## Discipline\n\nThe strategy only works if you keep going during downturns, which is exactly when it is psychologically hardest. Automation helps me stay the course."),
            ("How to Read a Company Balance Sheet",
             "# Reading a Balance Sheet\n\n## The Equation\n\nAssets equal liabilities plus equity. This identity always holds and is the foundation of the statement.\n\n## What to Look For\n\nI check the ratio of current assets to current liabilities for short-term health, and the level of long-term debt relative to equity for solvency.\n\n## Trends\n\nOne balance sheet is a snapshot; comparing several years reveals whether a company is strengthening or slowly weakening. Numbers tell a story over time."),
            ("That Unforgettable World Cup Final",
             "# The World Cup Final\n\n## The Tension\n\nThe match swung back and forth until the final over. As a fan I could barely watch, heart pounding with every delivery.\n\n## The Turning Point\n\nA sharp run-out and a cool-headed finish under enormous pressure decided it. Big players rise on the biggest stage.\n\n## Why Sport Matters\n\nMillions shared that single moment across the country. Few things unite people like a great final."),
            ("The Best Filter Coffee in Chennai",
             "# Filter Coffee in Chennai\n\n## The Ritual\n\nStrong decoction brewed in a steel filter, mixed with hot frothy milk and just enough sugar, poured between tumbler and davara to cool. The pour is part of the pleasure.\n\n## Where to Go\n\nThe old mess halls in Mylapore still serve the most authentic version, thick and aromatic. Freshly ground beans make all the difference.\n\n## Why It Matters\n\nFilter coffee is a daily ritual, not just a drink. It is the taste of a Chennai morning."),
        ],
    },
]


def main():
    print("Seeding DEV environment (LocalStack) — 5 users, weighted-interest blogs\n")
    for u in USERS:
        email = f"{u['username']}@gmail.com"
        password = f"{u['username']}@password@123"
        auth = signup_or_login(email, password, u["name"], u["domain"])
        token = auth["token"]
        tenant = auth["user"]["tenant_id"]
        print(f"● {u['name']}  <{email}>  → tenant {tenant}")
        for title, content in u["posts"]:
            r = create_post(token, title, content)
            print(f"    + {title[:45]:45}  {r.get('post_id')}  [{r.get('status')}]")
        print()
    print("Seed enqueued. The dev_worker will ingest each post into Qdrant (multitenant_chunks_dev).")


if __name__ == "__main__":
    main()
