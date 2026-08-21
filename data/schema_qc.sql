-- What would stop a take being used.
-- Blocking rows remove the take from coverage, so a scene can be short even
-- with footage apparently in the can.

CREATE TABLE IF NOT EXISTS the_gate.take_problems
(
    production_id   LowCardinality(String),
    scene_id        String,
    setup_id        String,
    take_id         String,
    category        LowCardinality(String),   -- crew_or_equipment | anachronism | focus | ...
    severity        LowCardinality(String),   -- blocking | warning | note
    what            String,
    where_in_frame  String,
    at_seconds      Float32,                  -- -1 if it applies throughout
    confidence      Float32,
    model_id        LowCardinality(String),
    checked_at      DateTime
)
ENGINE = MergeTree
ORDER BY (scene_id, take_id, severity);
