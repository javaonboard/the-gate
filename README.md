# THE GATE

### Check the gate.

Autonomous shoot-day operations for a film production in progress.

---

## The problem

The most expensive mistake in production is wrapping a setup you can't get back.

You find out weeks later, in the edit. There's no clean single on the second half of the scene. The window comp has no clean plate. By then the set is struck, the actor is on another job, and the location is thirty thousand dollars a day to re-rent. What should have been another twenty minutes on the floor becomes a pickup day.

Every one of those mistakes is made in the same five-second window — the moment the 1st AD calls *"check the gate,"* and the company moves on.

At the same time, that AD is carrying a second problem in their head: the clock. Sunset. Twelve-hour turnaround. A meal penalty every six hours. Minors' hours. Weather. A day of principal photography costs between fifty thousand and half a million dollars, and the person deciding whether to push for one more setup is doing it on instinct and a wristwatch.

THE GATE holds both of those at once, and — this is the part that matters — it holds them *together*.

Missing coverage only matters relative to the time you have left. Time only matters relative to what you're still missing. Ask either question alone and you get an answer that sounds reasonable and costs you money. Ask them together and you get something an AD can actually act on:

> *You have 22 minutes before turnaround and three shots missing. Grab the Marcus single and the clean plate — skip the insert, it can be shot anywhere. In that order it saves $47,000.*

---

## Why this isn't a chatbot

A conversational assistant can talk about your shoot. It can't run one.

| What the job needs | Why chat can't do it |
|---|---|
| Know how long *this* crew takes to light a night interior | No memory of your production — this needs millions of rows of your own history |
| React when rain arrives at 2pm | Nothing can push information *to* a chat window; it only answers when asked |
| Say *how likely* you are to make the day | Requires simulation over real distributions, not an opinion |
| Tell the crew, where the crew already looks | Needs write access to the systems a production actually runs on |
| Stay awake for a twelve-hour day | There is no event loop in a conversation |

THE GATE is built as a system, not a prompt. The numbers come from computation over real data. The reasoning sits on top.

---

## What it does

**Check the gate — the coverage side.** As proxies come off the camera card, Gemini reads the slate to identify scene, setup and take, then analyses the footage: shot size, camera movement, who's in frame, eyeline, focus, whether the take completed. It works out from the script what an editor will actually need to cut the scene, tracks what's been captured against that, checks the continuity geometry, and confirms the VFX plates are there. Then it makes a call — go or no-go — with anything missing ranked by what it will cost to get later.

**Make the day — the clock side.** A Monte Carlo simulation runs the remaining scenes ten thousand times, drawing setup durations from what this crew has actually done before, conditioned on live weather and light, and constrained by a union rule engine that knows about turnaround and meal penalties. It returns a probability, an expected overtime cost, and re-orderings that don't break continuity.

---

## Where the history comes from

The simulator is only as good as its memory, which raises a fair question: on day one of a shoot, there is no history.

That's true, and it resolves faster than you'd think.

**Day one** runs on the 1st AD's own numbers. Every production already has a schedule with an estimated duration for every setup — that estimate *is* the prior. THE GATE starts there rather than pretending to know better.

**By day three or four**, it has watched this crew work. Real durations replace estimates, and because a feature runs thirty to sixty days and an episode eight, the system is learning from actual performance well inside the first week. It sharpens every day.

**A studio never starts cold at all.** Studios shoot series and slates. Season one teaches season two. The same DPs, gaffers and crews come back. That accumulated memory across productions is the thing an individual AD can hold only in their head and only for the shows they personally worked on — and it's the reason this is a studio product rather than an indie one.

---

## Demo data

Two sources, and the distinction is deliberate.

**Today's shoot day is real.** The footage is from [Tears of Steel](https://mango.blender.org/), the Blender Foundation's live-action open movie, released under CC-BY along with all of its original camera negative and VFX plates. Real takes, real coverage, real plate material — analysed by Gemini exactly as the product would in production.

**The history behind it is generated.** You cannot film six weeks of a feature to demonstrate a hackathon project. `data/generate.py` produces a plausible production at realistic scale, with durations shaped by published industry figures — three to five pages a day, seven or eight setups for a dialogue two-hander, around three takes per setup at a 12:1 shooting ratio, twelve-hour days.

The extraction pipeline is real. The past it draws on is simulated, and it says so.

---

## Architecture

```
Parallel Monitor webhook ─┐
Cloud Scheduler tick ─────┤
New footage on the card ──┤
                          ▼
              Orchestrator  (ADK on Vertex AI Agent Engine)
                          │
  ┌────────────┬──────────┼──────────┬────────────┬────────────┐
  ▼            ▼          ▼          ▼            ▼            ▼
Continuity  Historian   Vision     Scout     Compliance   Control Room
scene deps  ClickHouse  Gemini    Parallel   union rules   Grafana MCP
180°/eyeline  via MCP   per take   Monitor/   turnaround    metrics,
            distributions          Task/      meals,        alerts,
                                   Search     minors        incidents
                          │
                          ▼
            Day Simulator — 10,000 trial Monte Carlo
                  (numpy, not a language model)
```

**ClickHouse** is the production's memory. Every setup, take, per-second frame and analysis. `quantilesTDigest` builds the duration distributions the simulator samples from; `ASOF JOIN` lines takes up against whatever the world was doing at that moment.

**Grafana** is its nervous system and its hands. The agent pushes live shoot-day metrics, writes its own alert rules when it spots a new risk, annotates the timeline, and declares an incident when a gate call comes back no-go — narrating its reasoning into the incident as it goes.

**Parallel** is everything outside the fence. Monitor subscriptions push weather, road closures, permit changes and union bulletins in as they happen. Task API does the cited research, and the citations are shown in the interface rather than buried.

**Gemini** reads the slate, watches the footage, and does the reasoning that ties the three together.

---

## Repository

```
the-gate/
├── agents/     ADK agents
├── core/       simulator, union rules, coverage, continuity
├── data/       ClickHouse schema and the production generator
├── api/        FastAPI — webhook receiver and UI backend
└── web/        the control room
```

## Running it

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
cp .env.example .env      # then fill in credentials
python data/generate.py --days 30
```

## Licence

Apache-2.0.
