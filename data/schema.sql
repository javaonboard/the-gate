-- THE GATE — ClickHouse schema
-- Run against database `the_gate`.

CREATE DATABASE IF NOT EXISTS the_gate;

-- ---------------------------------------------------------------------------
-- Reference: script and plan
-- ---------------------------------------------------------------------------

-- One row per production in the studio's library.
-- 1000 shoot days is not one film — it is ~18 productions over several years,
-- sharing a recurring crew pool. That is what makes the history meaningful.
CREATE TABLE IF NOT EXISTS the_gate.productions
(
    production_id   LowCardinality(String),
    title           String,
    kind            LowCardinality(String),     -- feature | series
    start_date      Date,
    end_date        Date,
    shoot_days      UInt16,
    primary_dp      LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (production_id);

-- One row per scene in the screenplay.
CREATE TABLE IF NOT EXISTS the_gate.scenes
(
    production_id   LowCardinality(String),
    scene_id        String,
    script_page     Float32,
    page_eighths    UInt16,                     -- industry length unit
    int_ext         LowCardinality(String),     -- INT | EXT | INT/EXT
    day_night       LowCardinality(String),     -- DAY | NIGHT | DUSK | DAWN
    scene_type      LowCardinality(String),     -- dialogue | action | stunt | vfx | montage
    location_id     LowCardinality(String),
    characters      Array(LowCardinality(String)),
    synopsis        String
)
ENGINE = MergeTree
ORDER BY (scene_id);

-- What an editor needs to cut each scene. Derived from the script by Gemini.
CREATE TABLE IF NOT EXISTS the_gate.scene_requirements
(
    scene_id            String,
    req_id              String,
    shot_type           LowCardinality(String), -- master | single | ots | insert | reaction | establishing | plate
    subject             LowCardinality(String), -- character name, prop, or '-'
    priority            UInt8,                  -- 1 = must have, 3 = nice to have
    recover_cost_usd    UInt32,                 -- cost to get this shot later
    is_vfx_plate        UInt8
)
ENGINE = MergeTree
ORDER BY (scene_id, req_id);

-- One row per shooting day.
CREATE TABLE IF NOT EXISTS the_gate.shoot_days
(
    production_id   LowCardinality(String),
    shoot_day       Date,
    unit            LowCardinality(String),     -- main | second | splinter
    location_id     LowCardinality(String),
    call_time       DateTime,
    wrap_time       Nullable(DateTime),
    sunrise         DateTime,
    sunset          DateTime,
    planned_scenes  Array(String)
)
ENGINE = MergeTree
ORDER BY (shoot_day, unit);

-- ---------------------------------------------------------------------------
-- Production facts
-- ---------------------------------------------------------------------------

-- One row per camera setup (position + lighting configuration).
-- Feeds the duration distributions the simulator samples from.
CREATE TABLE IF NOT EXISTS the_gate.setups
(
    production_id       LowCardinality(String),
    shoot_day           Date,
    scene_id            String,
    setup_id            String,
    start_ts            DateTime,
    end_ts              Nullable(DateTime),
    planned_duration_s  UInt32,
    actual_duration_s   UInt32,
    location_id         LowCardinality(String),
    int_ext             LowCardinality(String),
    day_night           LowCardinality(String),
    scene_type          LowCardinality(String),
    extras_count        UInt16,
    extras_bucket       UInt8 MATERIALIZED multiIf(extras_count = 0, 0, extras_count <= 5, 1, extras_count <= 20, 2, extras_count <= 50, 3, 4),
    dp_id               LowCardinality(String),
    crew_size           UInt16,
    shot_size           LowCardinality(String) DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(shoot_day)
ORDER BY (scene_id, setup_id);

-- One row per take. Camera metadata is constant across a take, so it lives here.
CREATE TABLE IF NOT EXISTS the_gate.takes
(
    production_id       LowCardinality(String),
    shoot_day           Date,
    scene_id            String,
    setup_id            String,
    take_no             UInt16,
    take_id             String,                 -- stable key used by analysis tables
    camera_roll         LowCardinality(String), -- e.g. A001
    clip_name           String,                 -- e.g. A001_C012
    tc_start            String,                 -- timecode, HH:MM:SS:FF
    tc_end              String,
    duration_s          Float32,
    lens_mm             Float32,
    t_stop              Float32,
    nd                  LowCardinality(String),
    iso                 UInt32,
    fps                 Float32,
    camera_body         LowCardinality(String),
    status              LowCardinality(String), -- complete | aborted | false_start
    circled             UInt8,                  -- script supervisor's preferred take
    slate_confidence    Float32,                -- Gemini's confidence reading the slate
    proxy_uri           String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(shoot_day)
ORDER BY (scene_id, setup_id, take_no);

-- Gemini's per-take analysis. One row per take.
CREATE TABLE IF NOT EXISTS the_gate.take_analysis
(
    production_id       LowCardinality(String),
    shoot_day           Date,
    scene_id            String,
    setup_id            String,
    take_id             String,
    shot_size           LowCardinality(String), -- ELS | LS | MLS | MS | MCU | CU | ECU
    movement            LowCardinality(String), -- static | pan | tilt | dolly | handheld | crane | steadicam
    subjects            Array(LowCardinality(String)),
    screen_direction    LowCardinality(String), -- left | right | neutral
    eyeline_target      LowCardinality(String),
    focus_score         Float32,                -- 0..1 mean across take
    exposure_score      Float32,
    continuity_flags    Array(String),          -- e.g. ['prop_moved','wardrobe_mismatch']
    vfx_clean_plate     UInt8,
    vfx_chart           UInt8,
    vfx_grey_ball       UInt8,
    vfx_markers         UInt8,
    vfx_lens_grid       UInt8,
    model_id            LowCardinality(String),
    analysed_at         DateTime
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(shoot_day)
ORDER BY (scene_id, setup_id, take_id);

-- Per-second analysis. THE large table — keep it narrow.
-- Only columns that genuinely vary within a take belong here.
CREATE TABLE IF NOT EXISTS the_gate.take_frames
(
    shoot_day       Date,
    scene_id        String,
    setup_id        String,
    take_id         String,
    t_seconds       UInt16,
    focus_score     Float32,
    exposure_score  Float32,
    shot_size       LowCardinality(String),     -- changes during a move
    subjects_count  UInt8,
    boom_visible    UInt8,
    flicker         UInt8
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(shoot_day)
ORDER BY (scene_id, setup_id, take_id, t_seconds);

-- Embeddings for shot-matching and continuity lookup.
-- Deliberately NOT per-second: multimodalembedding is 1408 dims, so per-second
-- across a season would be ~280 GB. One per take plus one per detected shot
-- change keeps this in the hundreds of MB.
CREATE TABLE IF NOT EXISTS the_gate.take_embeddings
(
    shoot_day   Date,
    scene_id    String,
    setup_id    String,
    take_id     String,
    t_seconds   UInt16,
    embedding   Array(Float32),
    INDEX idx_embedding embedding TYPE vector_similarity('hnsw', 'cosineDistance', 1408) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(shoot_day)
ORDER BY (scene_id, setup_id, take_id, t_seconds);

-- ---------------------------------------------------------------------------
-- Compliance and the outside world
-- ---------------------------------------------------------------------------

-- Crew clock in/out, for turnaround and meal penalty checks.
CREATE TABLE IF NOT EXISTS the_gate.crew_hours
(
    production_id   LowCardinality(String),
    shoot_day       Date,
    person_id       LowCardinality(String),
    department      LowCardinality(String),
    call_ts         DateTime,
    wrap_ts         Nullable(DateTime),
    meal_breaks     Array(DateTime),
    is_minor        UInt8,
    union_local     LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(shoot_day)
ORDER BY (shoot_day, department, person_id);

-- Events pushed in by Parallel Monitor webhooks.
-- ts is LAST in the ORDER BY so ASOF JOIN against takes/setups works.
CREATE TABLE IF NOT EXISTS the_gate.world_events
(
    location_id     LowCardinality(String),
    ts              DateTime,
    source          LowCardinality(String),     -- parallel_monitor | parallel_task | manual
    kind            LowCardinality(String),     -- weather | road_closure | permit | local_event | union_bulletin
    severity        UInt8,                      -- 1 low .. 5 blocking
    summary         String,
    citation_url    String,
    monitor_id      String,
    payload         String                      -- raw JSON
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (location_id, ts);

-- ---------------------------------------------------------------------------
-- Materialized view: setup duration distributions
-- This is what the Monte Carlo simulator samples from.
-- ---------------------------------------------------------------------------

-- Framing is a dimension because it is a cost. Lighting a wide means lighting
-- the whole space and clearing the floor of stands and cases; a close-up off a
-- position already lit is a move-in. Without this the simulator priced an
-- extreme close-up and an extreme long shot the same, and every missing shot
-- came back costing an identical amount to grab.
CREATE TABLE IF NOT EXISTS the_gate.setup_duration_stats
(
    dp_id           LowCardinality(String),
    int_ext         LowCardinality(String),
    day_night       LowCardinality(String),
    scene_type      LowCardinality(String),
    extras_bucket   UInt8,
    shot_size       LowCardinality(String),
    durations       AggregateFunction(quantilesTDigest(0.1, 0.25, 0.5, 0.75, 0.9), Float32),
    n               AggregateFunction(count)
)
ENGINE = AggregatingMergeTree
ORDER BY (dp_id, int_ext, day_night, scene_type, extras_bucket, shot_size);

CREATE MATERIALIZED VIEW IF NOT EXISTS the_gate.setup_duration_mv
TO the_gate.setup_duration_stats
AS
SELECT
    dp_id,
    int_ext,
    day_night,
    scene_type,
    extras_bucket,
    shot_size,
    quantilesTDigestState(0.1, 0.25, 0.5, 0.75, 0.9)(toFloat32(actual_duration_s)) AS durations,
    countState() AS n
FROM the_gate.setups
WHERE actual_duration_s > 0
GROUP BY dp_id, int_ext, day_night, scene_type, extras_bucket, shot_size;
