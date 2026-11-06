"""Static V4.0 training data for Spoken English Lab (stdlib only, zero app/db imports)."""
from __future__ import annotations

ROOMS: tuple[str, ...] = ("casual", "diplomatic", "executive", "analytical")
FLAVORS: tuple[str, ...] = ("neutral", "warm", "witty")
DIRECTNESS: tuple[str, ...] = ("soft", "balanced", "direct")
MODES: tuple[str, ...] = ("casual", "warm", "witty", "diplomatic", "executive", "analytical")

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Open active Room with World Card and Alex opener"),
    ("scene", "Pick from 12 structured Scene Contracts across 4 Rooms"),
    ("replay", "Replay Studio: hear the same idea across all 4 Rooms"),
    ("dilemma", "Practice 18 high-stakes spoken workout dilemmas"),
    ("podcast", "Listen to a Spoken Room Physics mini-podcast"),
    ("review", "Review flashcards, 32 Room Pivots, and 8 Drift guides"),
    ("debrief", "Get your 100-point Room Rubric debrief"),
    ("progress", "View your 3D Mastery Dashboard and C/D axes"),
    ("settings", "Open the 3D Control Panel (Room, Flavor, Directness)"),
)

SCENE_CARDS: dict[str, dict[str, str]] = {
    "casual": {
        "header": "🎧 CASUAL — Friends / Weekend / Small Talk",
        "world": "Friends, weekend plans, or everyday small talk—nothing at stake but connection.",
        "alex": "Alex talks like a friend: short, direct, phrasal verbs, lots of ellipsis.",
        "judged_on": "Does it sound like a text or voice note to a friend, not an essay?",
    },
    "warm": {
        "header": "💛 WARM — Care / Bad News / Checking In",
        "world": "Checking in on someone, reacting to bad news, or apologizing warmly.",
        "alex": "Alex talks warm and soft: empathy first, gentle hedging, unhurried pace.",
        "judged_on": "Does it feel genuinely caring, not clinical or cold?",
    },
    "witty": {
        "header": "😏 WITTY — Teasing / Banter / Comebacks",
        "world": "Playful teasing, dry banter, and humble comebacks among friends.",
        "alex": "Alex talks playful: dry humor, understatement, quick volleys.",
        "judged_on": "Does it land as a light joke, not mean or try-hard?",
    },
    "diplomatic": {
        "header": "🕊️ DIPLOMATIC — Disagreeing / Saying No / Sensitive Topics",
        "world": "Delicate moments with a boss, peer, or client where relationship and truth both matter.",
        "alex": "Alex talks careful: softeners, acknowledging your point first, framing constraints.",
        "judged_on": "Can you disagree or decline without sounding blunt, rude, or robotic?",
    },
    "executive": {
        "header": "💼 EXECUTIVE — Meetings / Decisions / Pushing Back",
        "world": "Running the room, killing bad plans, and making clear calls under time pressure.",
        "alex": "Alex talks clear and brief: no filler, decision-first, calm authority.",
        "judged_on": "Can you say it in 1-2 lines with clear ownership and zero buzzwords?",
    },
    "analytical": {
        "header": "🧠 ANALYTICAL — Explaining / Challenging / Reasoning",
        "world": "Stress-testing claims, walking through causal mechanisms, and weighing trade-offs.",
        "alex": "Alex talks precise: 'Here's the thing,' 'The issue is,' spoken logical signposts.",
        "judged_on": "Is your causal logic clear out loud, not just written academic prose?",
    },
}

SCORING_WEIGHTS: dict[str, dict[str, int]] = {
    "casual": {
        "Spoken Naturalness": 25,
        "Phrasal & Collocation Control": 20,
        "Grammatical Control": 15,
        "Pragmatic Warmth": 15,
        "Register Alignment": 15,
        "Conversational Flow": 10,
    },
    "diplomatic": {
        "Pragmatic & Face Management": 25,
        "Register & Tact Alignment": 20,
        "Spoken Naturalness": 15,
        "Hedging & Bridge Control": 15,
        "Grammatical Control": 15,
        "Constructive Next Step": 10,
    },
    "executive": {
        "Decision & Ownership Clarity": 25,
        "Spoken Brevity (Bottom-Line First)": 15,
        "Lexical Precision (Zero Buzzwords)": 15,
        "Grammatical Control": 15,
        "Pragmatic Authority": 15,
        "Register Alignment": 15,
    },
    "analytical": {
        "Causal Mechanism & Reasoning": 25,
        "Spoken Signposting": 15,
        "Lexical Precision": 15,
        "Grammatical Control": 15,
        "Epistemic Calibration": 15,
        "Register Alignment": 15,
    },
}
# end part 1

DILEMMAS: dict[str, tuple[dict[str, object], ...]] = {
    "casual": (
        {"id": "casual_1", "room": "casual", "flavor": "neutral", "title": "Casual • Bailing on Plans",
         "setup": "Your friend wants to go to a crowded club tonight. You are wiped and want to stay in without sounding flaky or rude.",
         "alex": "Yo, we're hitting that new spot tonight. You in?",
         "fails": "I would prefer to remain at home. / No, I don't want to go.",
         "winning": ("Ah man, I think I'm gonna stay in tonight. I'm wiped.", "Eh, I think I'm gonna pass tonight. Rain check?")},
        {"id": "casual_2", "room": "casual", "flavor": "neutral", "title": "Casual • Reacting to Bad News",
         "setup": "Your friend just told you his car broke down and the repair is $900. React like a friend, not a textbook.",
         "alex": "Dude, my car just died. Mechanic said $900 to fix it.",
         "fails": "That is very unfortunate. I am sorry to hear that.",
         "winning": ("Oh man, that's brutal. $900? That's rough.", "Ugh, that's the worst. Are you gonna get it fixed?")},
        {"id": "casual_3", "room": "casual", "flavor": "neutral", "title": "Casual • Storytelling a Small Annoyance",
         "setup": "Your neighbor's dog barked all night. Tell it as a natural story, not a formal complaint.",
         "alex": "You look tired. What happened?",
         "fails": "The dog of my neighbor was barking during the whole night, therefore I could not sleep.",
         "winning": ("Man, my neighbor's dog would not shut up all night. I barely slept.", "Dude, the dog next door was going off all night. I was up till like 3.")},
    ),
    "warm": (
        {"id": "warm_1", "room": "casual", "flavor": "warm", "title": "Warm • Checking on a Friend After a Fire",
         "setup": "Your friend lives near Jijel where there were wildfires. Text to check in with warmth.",
         "alex": "Hey, I saw the news about the fires near you. You good?",
         "fails": "I hope you are safe. Please inform me of your situation.",
         "winning": ("Hey, I just saw the news. Are you guys okay? Thinking of you.", "I saw what happened near Jijel. Just wanted to check in — you and your family okay?")},
        {"id": "warm_2", "room": "casual", "flavor": "warm", "title": "Warm • Comforting After a Breakup",
         "setup": "Your friend just got dumped. Be present and warm, do not try to fix it with advice.",
         "alex": "Yeah... we broke up. I don't really wanna talk about it.",
         "fails": "You will find someone better. You should move on.",
         "winning": ("I'm really sorry. I'm here if you wanna talk, or not talk. Either way.", "Oh no, I'm so sorry. That sucks. Want me to come over?")},
        {"id": "warm_3", "room": "casual", "flavor": "warm", "title": "Warm • Apologizing for Forgetting",
         "setup": "You forgot your friend's birthday. Give a warm, real apology, not a corporate one.",
         "alex": "So... you forgot my birthday, huh?",
         "fails": "I sincerely apologize for this oversight.",
         "winning": ("Oh my god, I'm so sorry. I totally blanked. I'm the worst.", "I know, I'm so sorry. I feel terrible. Can I make it up to you?")},
    ),
    "witty": (
        {"id": "witty_1", "room": "casual", "flavor": "witty", "title": "Witty • Teasing a Friend Who's Late",
         "setup": "Your friend is 40 minutes late again. Tease him playfully without fighting.",
         "alex": "Sorry I'm late again, traffic was insane.",
         "fails": "You are always late. This is unacceptable.",
         "winning": ("Traffic? Again? You live like 10 minutes away.", "Oh, you made it. I was about to order without you.")},
        {"id": "witty_2", "room": "casual", "flavor": "witty", "title": "Witty • Roasting a Bad Idea",
         "setup": "Your friend wants to buy a $200 smart water bottle. Keep your roast playful.",
         "alex": "I'm thinking of getting that $200 smart water bottle.",
         "fails": "That is a waste of money and a stupid idea.",
         "winning": ("$200 for water? Does it also do your taxes?", "Yeah, because my $10 bottle just isn't hydrated enough.")},
        {"id": "witty_3", "room": "casual", "flavor": "witty", "title": "Witty • Deflecting a Compliment",
         "setup": "Someone compliments your cooking. Be funny and humble, not stiff.",
         "alex": "Okay this is actually amazing. You're a chef now?",
         "fails": "Thank you very much. I am glad you enjoy it.",
         "winning": ("Don't tell anyone, it's just the frozen stuff with extra cheese.", "Right? I have no idea how I pulled that off.")},
    ),
# end part 2
    "diplomatic": (
        {
            "id": "diplomatic_1", "room": "diplomatic", "flavor": "neutral",
            "title": "Diplomatic • Saying No to a Boss",
            "setup": "Your boss wants you to work this weekend. Say no while staying professional and warm.",
            "alex": "Hey, can you cover this weekend? I know it's short notice.",
            "fails": "No, I cannot. I have plans. / I would be unable to comply due to personal commitments.",
            "winning": (
                "I wish I could, but I already have plans I can't move. Is there another way we can cover it?",
                "I totally get it's short notice — I just can't swing this weekend. Can I help set it up for someone else?",
            ),
        },
        {
            "id": "diplomatic_2", "room": "diplomatic", "flavor": "neutral",
            "title": "Diplomatic • Disagreeing Without Fighting",
            "setup": "Your friend thinks the new plan at work is great. You think it will fail. Disagree without sounding argumentative.",
            "alex": "Honestly I think the new plan is gonna work great.",
            "fails": "You are wrong. It will not work. (Too direct)",
            "winning": (
                "I see where you're coming from, I just worry it might be a little rushed.",
                "Yeah, I get why you'd think that. I'm just not sure it's gonna land the way they think.",
            ),
        },
        {
            "id": "diplomatic_3", "room": "diplomatic", "flavor": "warm",
            "title": "Diplomatic • Giving Sensitive Feedback",
            "setup": "Your roommate keeps leaving dishes in the sink. Bring it up without starting a fight.",
            "alex": "Hey, did you leave those dishes again?",
            "fails": "You always leave dishes. It's disgusting. (Accusatory)",
            "winning": (
                "Hey, I know it's small, but could we try to keep the sink clear? It kinda piles up.",
                "No worries, but would you mind doing your dishes tonight? I got yesterday's.",
            ),
        },
    ),
    "executive": (
        {
            "id": "executive_1", "room": "executive", "flavor": "neutral",
            "title": "Executive • Killing a Bad Plan",
            "setup": "Someone proposes spending 3 weeks on a feature no one wants. Kill it clearly, calmly, in 2 lines.",
            "alex": "I think we should spend the next three weeks rebuilding the dashboard from scratch.",
            "fails": "With all due respect, I think we should circle back and leverage our synergies to move the needle.",
            "winning": (
                "I don't think that's worth three weeks. Let's ship the fix and move on.",
                "Let's not do that. It's too much time for too little payoff.",
            ),
        },
        {
            "id": "executive_2", "room": "executive", "flavor": "neutral",
            "title": "Executive • Pushing Back on a Deadline",
            "setup": "Your manager wants a report by tomorrow, which is impossible without cutting corners. Push back and propose an alternative.",
            "alex": "Can you get me that report by tomorrow morning?",
            "fails": "I will try my best to deliver it as soon as possible. / That's impossible.",
            "winning": (
                "Tomorrow's tight. I can get you a solid draft by Thursday, full version Friday. Does that work?",
                "I can't do it well by tomorrow. I can do it right by Friday.",
            ),
        },
        {
            "id": "executive_3", "room": "executive", "flavor": "neutral",
            "title": "Executive • Making a Decision in a Meeting",
            "setup": "The team is going in circles. Close the discussion and decide. Short, calm, final.",
            "alex": "So... are we doing A or B? We've been going back and forth for an hour.",
            "fails": "In my humble opinion, perhaps we should consider... (Over-hedged, weak)",
            "winning": (
                "Let's go with A. We can adjust if it doesn't work.",
                "We're going with A. Let's move on and test it.",
            ),
        },
    ),
    "analytical": (
        {
            "id": "analytical_1", "room": "analytical", "flavor": "neutral",
            "title": "Analytical • Challenging a Premise",
            "setup": "Your friend says 'Everyone hates remote work.' Challenge the idea, not the person.",
            "alex": "Honestly, everyone hates working from home now.",
            "fails": "That's not true. You're wrong. / Research indicates... (Too blunt / Academic)",
            "winning": (
                "I don't think it's everyone. I think it's more that people hate being stuck at home, not the work itself.",
                "Here's the thing — is it that they hate remote work, or that they hate how their company does it?",
            ),
        },
        {
            "id": "analytical_2", "room": "analytical", "flavor": "neutral",
            "title": "Analytical • Explaining Why Something Failed",
            "setup": "Your project failed. Explain the reason clearly out loud, in order, without reading an essay.",
            "alex": "So why do you think it flopped?",
            "fails": "It flopped due to various multifaceted factors. (Vague, written)",
            "winning": (
                "The issue was timing. We launched too late, and by then people had already moved on.",
                "Two things: we priced it too high, and we didn't really explain who it was for.",
            ),
        },
        {
            "id": "analytical_3", "room": "analytical", "flavor": "neutral",
            "title": "Analytical • Comparing Two Options",
            "setup": "Compare staying in your job vs leaving. Lay out the tradeoff clearly, like a native in conversation.",
            "alex": "So are you staying or leaving?",
            "fails": "On the one hand... on the other hand... (Textbook essay structure)",
            "winning": (
                "If I stay, it's stable but I'm bored. If I leave, it's risky but I actually learn something.",
                "Staying is safe, but I'm not growing. Leaving is scary, but at least it's a move.",
            ),
        },
    ),
}
# end part 3

SCENE_REGISTRY: dict[str, tuple[dict[str, object], ...]] = {
    "casual": (
        {
            "id": "catching_up_with_a_friend", "title": "☕ Catching Up With a Friend",
            "flavor": "warm", "directness": "balanced",
            "world": "Informal hangout with an old friend after some time apart.",
            "alex": "Relaxed, curious, quick backchannels and follow-ups.",
            "judged_on": "Natural spoken updates, phrasal verbs, contractions, zero interview stiffness.",
            "opener": "Hey, it's been a while! What's been going on with you lately?",
            "pivots": ("Been super busy with...", "Honestly, not much—just...", "Wait, no way!"),
        },
        {
            "id": "making_weekend_plans", "title": "🍕 Making or Bailing on Plans",
            "flavor": "neutral", "directness": "balanced",
            "world": "Two friends figuring out tonight's or this weekend's plans.",
            "alex": "Casual, low-pressure, tossing out ideas.",
            "judged_on": "Accepting or passing on plans naturally ('stay in', 'pass tonight', 'rain check').",
            "opener": "Yo, we're hitting that new spot tonight. You in, or are you staying in?",
            "pivots": ("I think I'm gonna stay in.", "I'm gonna pass tonight—rain check?", "I'm down if it's chill."),
        },
        {
            "id": "unexpected_problem", "title": "🚗 Reacting to Sudden Bad News",
            "flavor": "warm", "directness": "direct",
            "world": "Your friend's car just died and the repair is $900.",
            "alex": "Stressed friend venting about an unexpected $900 bill.",
            "judged_on": "Sounding like a real friend reacting out loud, not a formal sympathy card.",
            "opener": "Dude, my car just died. Mechanic said $900 to fix it.",
            "pivots": ("Oh man, that's brutal.", "Ugh, that's rough.", "What ended up happening?"),
        },
    ),
    "diplomatic": (
        {
            "id": "disagreeing_with_a_colleague", "title": "🤝 Disagreeing With a Colleague",
            "flavor": "neutral", "directness": "balanced",
            "world": "Workplace discussion where you disagree with a rushed Friday launch.",
            "alex": "Colleague defending the Friday launch but open to reason.",
            "judged_on": "Acknowledge first, name the risk, and propose a safer alternative.",
            "opener": "I think we should move ahead and launch this Friday—it's the fastest option.",
            "pivots": ("I like the momentum, but...", "The part that worries me is...", "What if we test it first?"),
        },
        {
            "id": "declining_a_request", "title": "🛡️ Declining Weekend Work",
            "flavor": "warm", "directness": "soft",
            "world": "Your manager asks you to cover this weekend on short notice.",
            "alex": "Manager under pressure asking for short-notice coverage.",
            "judged_on": "Setting a clear boundary while keeping goodwill and offering a bridge.",
            "opener": "Hey, can you cover this weekend? I know it's short notice, but we're short-handed.",
            "pivots": ("I wish I could, but...", "I can't swing this weekend.", "Can I help prep it Friday?"),
        },
        {
            "id": "giving_difficult_feedback", "title": "💬 Giving Sensitive Feedback",
            "flavor": "warm", "directness": "balanced",
            "world": "Addressing recurring late handoffs with a peer without starting a fight.",
            "alex": "Peer who doesn't realize their late handoffs are squeezing your QA window.",
            "judged_on": "Putting the problem on the timeline/situation rather than attacking the person.",
            "opener": "Hey, you wanted to talk about how the last sprint handoff went—everything okay?",
            "pivots": ("When it comes in late Thursday...", "Could we aim for Wednesday?", "I don't want us jammed on Friday."),
        },
    ),
    "executive": (
        {
            "id": "addressing_missed_deadline", "title": "⏱️ Addressing a Missed Deadline",
            "flavor": "neutral", "directness": "direct",
            "world": "Accountability check-in right after a missed project deadline.",
            "alex": "Senior lead asking for the headline and recovery date.",
            "judged_on": "Owning the delay ('That's on me'), naming the blocker briefly, and locking a date.",
            "opener": "We're past the deadline. Give me the headline—where do things stand right now?",
            "pivots": ("That's on me.", "Here's the blocker:", "We'll have it live by Thursday noon."),
        },
        {
            "id": "leading_project_update", "title": "📊 Ending a Spinning Meeting",
            "flavor": "neutral", "directness": "direct",
            "world": "The team has been debating Option A vs Option B for an hour.",
            "alex": "Colleague waiting for you to make the final call.",
            "judged_on": "Bottom-line decision first, one-line reason, clear owner and deadline.",
            "opener": "So... are we doing Option A or Option B? We've been going in circles for an hour.",
            "pivots": ("I'm making the call:", "We're going with A.", "You own the rollout—v1 by Friday."),
        },
        {
            "id": "challenging_a_proposal", "title": "⚡ Killing a 3-Week Time Sink",
            "flavor": "neutral", "directness": "direct",
            "world": "Someone pitches spending 3 weeks rebuilding a dashboard from scratch.",
            "alex": "Colleague pitching a 3-week rebuild in a decision meeting.",
            "judged_on": "Killing a low-payoff plan in 1-2 crisp lines with zero MBA buzzwords.",
            "opener": "I think we should spend the next three weeks rebuilding the dashboard from scratch.",
            "pivots": ("Here's the constraint that kills it:", "Not worth three weeks.", "Ship the core fix and move on."),
        },
    ),
    "analytical": (
        {
            "id": "evaluating_a_claim", "title": "🔍 Challenging a Sweeping Premise",
            "flavor": "neutral", "directness": "balanced",
            "world": "Someone claims 'Everyone hates remote work now.'",
            "alex": "Sharp peer testing an overgeneralized claim.",
            "judged_on": "Drawing a distinction ('It's less X than Y') and testing the assumption aloud.",
            "opener": "Honestly, everyone hates working from home now—the remote experiment failed.",
            "pivots": ("It's less X than Y.", "That premise only holds if...", "Here's the tension:"),
        },
        {
            "id": "investigating_a_problem", "title": "🧩 Explaining Why Something Flopped",
            "flavor": "neutral", "directness": "direct",
            "world": "Post-mortem conversation on why a product launch failed.",
            "alex": "Analytical partner probing for the real causal mechanism.",
            "judged_on": "Spoken signposting ('Two things:') and clear cause-and-effect without essay prose.",
            "opener": "Looking at the numbers—why do you think the launch actually flopped?",
            "pivots": ("There are two things going on:", "The main issue was timing.", "Which meant users bounced."),
        },
        {
            "id": "comparing_competing_explanations", "title": "⚖️ Weighing Real Trade-Offs Live",
            "flavor": "neutral", "directness": "balanced",
            "world": "Comparing staying in a safe role vs taking a risky leap.",
            "alex": "Peer asking how you weigh stability against growth.",
            "judged_on": "Spoken conditional trade-offs ('If I stay... if I leave...') instead of 'On the one hand'.",
            "opener": "So are you staying or leaving? How are you weighing the trade-offs?",
            "pivots": ("If I stay, it's safe, but...", "If I jump, the upside is...", "It comes down to X vs Y."),
        },
    ),
}
# end part 4

PIVOTS: dict[str, tuple[dict[str, str], ...]] = {
    "casual": (
        {"phrase": "Yeah, that's not gonna fly.", "move": "Replaces: 'I am afraid that is not feasible.'"},
        {"phrase": "I mean, come on.", "move": "Replaces: 'Surely you must see the issue.'"},
        {"phrase": "Honestly? Not really.", "move": "Replaces: 'I must respectfully disagree.'"},
        {"phrase": "We're good. / We're solid.", "move": "Replaces: 'The situation is satisfactory.'"},
        {"phrase": "That's on me.", "move": "Replaces: 'I take full responsibility for the oversight.'"},
        {"phrase": "Hold up a sec.", "move": "Replaces: 'Please wait a moment while I consider.'"},
        {"phrase": "I think I'm gonna pass tonight.", "move": "Replaces: 'I prefer to remain at home this evening.'"},
        {"phrase": "Let's just do it.", "move": "Replaces: 'I propose we proceed with the plan.'"},
    ),
    "diplomatic": (
        {"phrase": "I like the direction, but...", "move": "Bridge first before raising a risk."},
        {"phrase": "Help me understand...", "move": "Invites explanation without sounding accusatory."},
        {"phrase": "That first part tracks—my worry is...", "move": "Validates the premise, isolates the risk."},
        {"phrase": "What would it take for this to work?", "move": "Turns a hard no into collaborative problem-solving."},
        {"phrase": "I was leaning the other way, but...", "move": "Concedes gracefully when someone makes a fair point."},
        {"phrase": "There's a version of this that works.", "move": "Keeps the door open while rejecting the current scope."},
        {"phrase": "I don't want us to get stuck here.", "move": "Breaks a deadlock without blaming the person."},
        {"phrase": "Can we park that for a second?", "move": "Defers a side issue warmly to protect focus."},
    ),
    "executive": (
        {"phrase": "Here's the constraint that kills it.", "move": "Kills a bad plan in one line with a real reason."},
        {"phrase": "I'm making the call.", "move": "Ends a spinning meeting with clear authority."},
        {"phrase": "We're moving on.", "move": "Closes a settled topic with zero filler."},
        {"phrase": "I own this.", "move": "Takes direct accountability without apology loops."},
        {"phrase": "Bottom line me.", "move": "Cuts through rambling to get the headline."},
        {"phrase": "That's not the priority right now.", "move": "Declines low-payoff work decisively."},
        {"phrase": "New information. Changing the call.", "move": "Pivots cleanly without defensiveness."},
        {"phrase": "Done. Next.", "move": "Locks in an action item and advances the room."},
    ),
    "analytical": (
        {"phrase": "Unless I'm missing a lever...", "move": "Calibrated epistemic hedge before challenging a claim."},
        {"phrase": "Walk me through the mechanism.", "move": "Probes how X actually causes Y."},
        {"phrase": "That premise only holds if...", "move": "Surfaces a hidden assumption cleanly."},
        {"phrase": "What's the actual claim we're testing?", "move": "Sharpens a vague argument into a testable point."},
        {"phrase": "If we run it that way, we hit a wall on...", "move": "Traces the second-order consequence out loud."},
        {"phrase": "I'm not seeing the causal link yet.", "move": "Separates correlation from causation."},
        {"phrase": "Let's stress-test that.", "move": "Challenges the idea, never the person."},
        {"phrase": "That data point undercuts the premise.", "move": "Shows exactly why new evidence changes the model."},
    ),
}

DRIFT_EXAMPLES: tuple[dict[str, str], ...] = (
    {"from_room": "casual", "to_room": "executive", "line": "Yeah, I don't really think we should do that right now, maybe later?", "why": "Sounds non-committal in Executive.", "upgrade": "That's not the priority right now. We're holding off."},
    {"from_room": "diplomatic", "to_room": "executive", "line": "I appreciate the idea, but I'm not sure the timing is ideal.", "why": "Too much cushion when the room needed a call.", "upgrade": "Timing doesn't work. We're not moving on this."},
    {"from_room": "textbook", "to_room": "diplomatic", "line": "I must respectfully decline due to resource constraints.", "why": "Reads like a cold legal refusal letter.", "upgrade": "I like where you're going with this, but we're stretched too thin right now."},
    {"from_room": "casual", "to_room": "analytical", "line": "Nah, that won't work.", "why": "Flat verdict with zero causal mechanism.", "upgrade": "If we run it that way, we hit a wall on cost—unless I'm missing a lever?"},
    {"from_room": "executive", "to_room": "diplomatic", "line": "No. We're not doing that.", "why": "Lands as a blunt door-slam in Diplomatic.", "upgrade": "I don't see a version of this that works right now—what if we tried a smaller pilot?"},
    {"from_room": "analytical", "to_room": "casual", "line": "The premise only holds if second-order effects remain linear.", "why": "Sounds like a seminar at a friend's kitchen table.", "upgrade": "Yeah, that only works if nothing else goes sideways—and it always does."},
    {"from_room": "diplomatic", "to_room": "executive", "line": "I hate to push back, but maybe we should consider another path?", "why": "Apologetic hedging undercuts authority.", "upgrade": "I'm pushing back on this. We need another path."},
    {"from_room": "textbook", "to_room": "analytical", "line": "Upon further analysis, the aforementioned approach is suboptimal.", "why": "Stiff written essay prose.", "upgrade": "I'm not seeing why this is the better path—walk me through the mechanism."},
)

REPLAY_INTENTIONS: tuple[dict[str, str], ...] = (
    {"id": "kill_idea", "label": "🛑 Tell someone their idea won't work", "idea": "I don't think this plan will work because of the cost.", "casual": "Yeah, that's not gonna fly—way too pricey.", "diplomatic": "I like the direction, but I don't think this version gets us there on budget. What if we scaled it back?", "executive": "This doesn't work. Cost is the constraint that kills it.", "analytical": "If we run the numbers that way, we hit a wall on cost—unless I'm missing a lever?"},
    {"id": "bail_plans", "label": "🛋️ Pass on tonight's plans", "idea": "I don't want to go to the club tonight.", "casual": "Eh, I think I'm gonna pass tonight.", "diplomatic": "I appreciate the invite, but I think I'm gonna sit this one out. Rain check?", "executive": "I'm gonna pass tonight. Let's catch up tomorrow.", "analytical": "I'm gonna stay in—I'm wiped, and going out won't help me recharge."},
    {"id": "not_finished", "label": "⏳ Admit you haven't finished yet", "idea": "I have not finished the report yet.", "casual": "Haven't finished it yet—still on it.", "diplomatic": "I'm not quite done yet—I wanted to get the core numbers right before sending it over.", "executive": "It's not done. That's on me. You'll have it by 3 PM.", "analytical": "Still working through it—the first assumption broke once I checked the new data."},
    {"id": "get_to_point", "label": "🎯 Ask someone to get to the point", "idea": "Please tell me the main point.", "casual": "Wait—what's the actual point?", "diplomatic": "I want to make sure I'm tracking—can you give me the core takeaway?", "executive": "Bottom line me. What's the call?", "analytical": "I'm tracking the details, but what's the actual claim we're testing here?"},
    {"id": "change_mind", "label": "🔄 Change your mind after new info", "idea": "The new facts changed my opinion.", "casual": "Okay, yeah—that totally changes it for me.", "diplomatic": "I was leaning the other way, but what you just walked through actually moves me.", "executive": "New information. I'm changing the call.", "analytical": "That data point undercuts my earlier premise—we need to adjust the model."},
    {"id": "push_deadline", "label": "📅 Push back on a tight deadline", "idea": "Friday is too soon to finish this properly.", "casual": "There's no way we're getting this done by Friday.", "diplomatic": "I get why Friday's the target, but rushing it risks bugs. Can we aim for Tuesday?", "executive": "Friday isn't realistic. We ship core Monday, full version Wednesday. I own it.", "analytical": "Friday only holds if QA takes zero hours—and QA takes two full days."},
)
# end part 5
# END v4_data
