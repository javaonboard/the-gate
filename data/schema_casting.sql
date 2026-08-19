-- Casting — who is in the footage.
-- Faces are cropped, embedded, and matched by cosine distance so the same
-- person found in two takes becomes one character without anyone typing a name.

CREATE TABLE IF NOT EXISTS the_gate.characters
(
    production_id   LowCardinality(String),
    character_id    String,
    name            String,                     -- what the AD called them
    face_uri        String,                     -- cropped face, served to the UI
    embedding       Array(Float32),             -- multimodalembedding of the face
    description     String,                     -- how Gemini described them
    first_take_id   String,
    appearances     UInt32,
    created_at      DateTime,
    INDEX idx_face embedding TYPE vector_similarity('hnsw', 'cosineDistance', 1408) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (production_id, character_id);

-- Which characters appear in which take, and where in frame.
CREATE TABLE IF NOT EXISTS the_gate.take_characters
(
    production_id   LowCardinality(String),
    scene_id        String,
    setup_id        String,
    take_id         String,
    character_id    String,
    confidence      Float32,
    bbox            Array(Float32),             -- x0, y0, x1, y1 normalised
    prominence      LowCardinality(String),     -- foreground | background
    matched_by      LowCardinality(String)      -- new | embedding | name
)
ENGINE = ReplacingMergeTree
ORDER BY (scene_id, take_id, character_id);

-- What each character needs, per scene. Rows the AD ticks on and off.
CREATE TABLE IF NOT EXISTS the_gate.character_requirements
(
    scene_id            String,
    character_id        String,
    shot_type           LowCardinality(String), -- wide | medium | close | ots
    required            UInt8,
    recover_cost_usd    UInt32,
    updated_at          DateTime
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (scene_id, character_id, shot_type);
