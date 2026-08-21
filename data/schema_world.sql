-- The world a production is set in.
-- Without this there is no such thing as an anachronism: a coffee cup is only
-- wrong because the scene is medieval. Somebody has to say which.

CREATE TABLE IF NOT EXISTS the_gate.production_world
(
    production_id   LowCardinality(String),
    period          String,     -- "1300s medieval Europe", "near-future 2040"
    setting         String,     -- "a fishing village", "a canal street"
    notes           String,     -- anything else that would look wrong
    updated_at      DateTime
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (production_id);
