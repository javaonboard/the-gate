# THE GATE

Shoot-day operations for a film crew. Drop a day's footage in and it tells you
whether you can move on.

---

## The problem

Before a film crew moves the camera, someone says **"check the gate."** It is
the last moment you can still get the shot.

If something is missing you find out weeks later, in the edit. By then the set
is struck, the actor is on another job, and the location costs thirty thousand
dollars a day to rent again. Twenty more minutes on the floor becomes a pickup
day.

At the same time the 1st AD is watching the clock — sunset, meal penalties,
turnaround, overtime. A day of shooting costs between fifty thousand and half a
million dollars.

Neither question means anything alone. Being short only matters against the
time you have left. The time left only matters against what is still missing.

THE GATE answers both together.

---

## What it does

**Drop a film in.** It cuts the file into shots, works out what each one is,
who is in it, and what would stop it being used. Nobody types a scene number, a
take number or a character name.

**Ask for the call.** It works out what each scene still needs, runs ten
thousand simulations of the rest of the day, checks the union rules, and prices
every missing shot two ways: what it costs to grab now, and what it costs to
come back for.

Then it says GO or NO-GO.

```
NO-GO   54% of the day · 7 of 13 shots · $136,000 at risk

close-up of Character A     $548 now    vs   $30,000 later
```

**Put it right when it is wrong.** Where one take ends and the next begins is a
judgment, and on raw camera-card footage it is a hard one — the camera rolls
through the resets, the director talks over the action, and a fight is shot
with the camera swinging hard enough to look like a cut. Measured against a
script supervisor watching the same ten minutes, the split has landed a third
under and two thirds over.

Nobody watching the footage is in any doubt. So the person who can see it says
so, once, and it stays said:

| It got wrong | You say |
|---|---|
| One take split into three | tick them, **join them** |
| A take filed under the wrong scene | tick it, **move to…** |
| One room read as two places | **merge scenes** |
| One actor read as two people | click a face, **this is someone already listed** |

The system proposes and the AD decides. That is the honest shape of it, and it
is why the numbers can be trusted: everything downstream — coverage, the odds,
the money — is computed from what the person confirmed, not from what a model
guessed.

---

## Why not a chatbot

| What the job needs | Why a chat window cannot |
|---|---|
| Know how long *this* crew takes on a night interior | No memory of your production |
| React when rain arrives at 2pm | Nothing can push information to it |
| Say how likely you are to make the day | Needs simulation over real data, not an opinion |
| Spot a collar tag that changed between takes | Needs to have watched every take |
| Stay awake for a twelve-hour day | A conversation has no event loop |

The rule the whole system follows: **computation for facts, models for
judgment.** Whether a scene is covered and what a delay costs are calculated in
Python. Models are only asked things a person would answer by looking.

---

## Runtime proof

Every service below is called while the system runs.

### Google Cloud

| What | Where |
|---|---|
| Gemini watches each take | [`agents/vision.py`](agents/vision.py) |
| Gemini finds what makes a take unusable | [`agents/qc.py`](agents/qc.py) |
| Gemini decides where each shot begins | [`agents/editor.py`](agents/editor.py) |
| Gemini works out who is in frame | [`agents/casting.py`](agents/casting.py) |
| Gemini checks two takes will cut together | [`agents/continuity.py`](agents/continuity.py) |
| `multimodalembedding` for face vectors | [`agents/casting.py`](agents/casting.py) |
| ADK agent, run on every gate check | [`agents/orchestrator.py`](agents/orchestrator.py) |
| Forced function calling, so the rules cannot be skipped | [`agents/compliance.py`](agents/compliance.py) |

### ClickHouse

| What | Where |
|---|---|
| Official MCP server, read by the agent | [`agents/historian.py`](agents/historian.py) |
| Direct connection for ingest and coverage | [`core/coverage.py`](core/coverage.py) |

The agent reads only the five curated views in `the_gate_marts`, as a read-only
user. `SELECT` on a raw table is refused.

### Parallel

| What | Where |
|---|---|
| **Search API**, called on every gate check | [`agents/scout.py`](agents/scout.py) |
| Task API for cited research | [`agents/scout.py`](agents/scout.py) |
| Monitor API for standing webhooks | [`agents/scout.py`](agents/scout.py) |

The search is not a tool the model may reach for. It runs every time, from
[`agents/orchestrator.py`](agents/orchestrator.py), and the answer lands in
`world_events` with its source URL. Whether the street is closed should not
depend on an agent remembering to look.

### Computed, not generated

| What | Where |
|---|---|
| Monte Carlo over the rest of the day | [`core/simulator.py`](core/simulator.py) |
| Union rules — meals, turnaround, overtime, minors | [`core/union_rules.py`](core/union_rules.py) |
| Coverage, per person and per scene | [`core/character_coverage.py`](core/character_coverage.py) |
| The GO / NO-GO itself | [`core/gate.py`](core/gate.py) |

A GO is never returned without the rule check having run.

---

## How it fits together

```
a film                                          the call
   │                                               ▲
   ▼                                               │
Editor ─ Scripty ─ QC ─ Breakdown ─ Casting    The Gate
   │        │       │       │          │           │
   └────────┴───────┴───────┴──────────┘      Scout · Book · Clock · Steward
                    │                              │
                ClickHouse ─────────────────────────
```

Six agents take the footage apart, once, when it arrives. Six more answer the
question every time the AD asks. Everything lands in ClickHouse.

---

## Running it

Needs Python 3.12, Node 22, ffmpeg, and a `.env` — copy `.env.example`.

```powershell
uv venv
uv pip install -r requirements.txt
python -m data.schema
uvicorn api.main:app --reload --port 8080
```

In a second terminal:

```powershell
cd web
npm install
npm run dev
```

Then open http://localhost:5173.

The demo day is shared, so it is read-only — a visitor's edit would change what
everyone else sees. Whoever curates it can set `DEMO_EDITABLE=1` in `.env` to
correct it in place. Leave it unset wherever this is deployed.

The ClickHouse MCP server runs separately, as the read-only agent user:

```powershell
.\run_mcp.ps1
```

---

## Deploying it

Build it locally first. Docker Desktop has to be running.

```powershell
docker build -t the-gate .
```

The container has no Google credentials of its own, so hand it yours. On Cloud
Run this is not needed, the service account is picked up automatically.

```powershell
docker run -p 8080:8080 --env-file .env `
  -v "$env:APPDATA\gcloud\application_default_credentials.json:/adc.json:ro" `
  -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json `
  the-gate
```

Then open http://localhost:8080. Note 8080, not 5173: in the container the API
serves the interface itself.

Takes will not play until a bucket is mounted, see below. Everything else is
real, because the analysis lives in ClickHouse rather than in the image.

One container: the API serves the built interface, so there is one origin and
no CORS.

```powershell
gcloud run deploy the-gate `
  --source . `
  --region us-central1 `
  --allow-unauthenticated `
  --min-instances 1 --max-instances 1 `
  --cpu-boost --no-cpu-throttling `
  --memory 2Gi --timeout 3600 `
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$env:GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=global,CLICKHOUSE_HOST=$env:CLICKHOUSE_HOST,CLICKHOUSE_DATABASE=the_gate" `
  --set-secrets "CLICKHOUSE_PASSWORD=clickhouse-password:latest,PARALLEL_API_KEY=parallel-key:latest"
```

Three flags matter:

- **`--max-instances 1`** — runs in flight are held in memory and streamed to
  the browser. A second instance would answer with a run it has never heard of.
- **`--no-cpu-throttling`** — taking a film in continues after the request has
  returned. Throttled, it stalls.
- **`--timeout 3600`** — uploads are large.

Then seed the demo day:

```powershell
python -m data.seed_demo --film ..\footage\horror-10min.mp4
```

### Where the footage lives

The image has no footage in it. Clips and face crops are written while the
system runs and have to outlive the container, so both are bucket mounts:
`/footage` and `/faces`. Cloud Run presents a bucket as a directory, so nothing
in the code changes.

```powershell
gcloud storage buckets create gs://the-gate-footage --location us-central1
```

Add to the deploy:

```powershell
  --add-volume "name=footage,type=cloud-storage,bucket=the-gate-footage" `
  --add-volume-mount "volume=footage,mount-path=/footage" `
  --add-volume "name=faces,type=cloud-storage,bucket=the-gate-faces" `
  --add-volume-mount "volume=faces,mount-path=/faces"
```

Then seed the demo, which fills both.

---

## Repository

```
agents/     the crew — one file each, plus intake.py, the pipeline
core/       everything with a correct answer: coverage, rules, simulation
api/        routes by subject, and the live event stream
data/       schema, generators, and the demo seed
web/        the control room
```

---

## Demo footage

The demo day is raw camera-card footage from
[Cinestudy](https://cinestudy.org) — real slates, several takes of one setup,
and takes that genuinely cannot be used. A finished film has none of those,
because everything in it was already chosen.

Footage © Sonnyboo, used with permission for editing projects. #Cinestudy

Footage is never committed. It lives in `footage/`, a sibling of this
repository, so the mistake is not possible.

---

## Licence

Apache 2.0. See [LICENSE](LICENSE).
