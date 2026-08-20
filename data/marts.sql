-- The marts layer — what the agent is allowed to see.
--
-- ClickHouse's own guidance for agentic analytics: expose curated marts, not
-- raw tables, so the model works from stable canonical definitions rather than
-- re-deriving business logic in generated SQL. Every view here answers a
-- question someone on a set would actually ask.

CREATE DATABASE IF NOT EXISTS the_gate_marts;

-- One row per scene: where it is, what has been shot, who is in it.
CREATE OR REPLACE VIEW the_gate_marts.scene_status
DEFINER = default SQL SECURITY DEFINER AS
SELECT
    s.scene_id                                   AS scene_id,
    replaceAll(s.location_id, '_', ' ')          AS place,
    if(s.int_ext = 'INT', 'inside', 'outside')   AS inside_or_outside,
    lower(s.day_night)                           AS time_of_day,
    s.scene_type                                 AS scene_type,
    uniqExact(t.take_id)                         AS takes_shot,
    uniqExact(t.setup_id)                        AS camera_positions,
    uniqExact(tc.character_id)                   AS people_on_camera,
    round(avg(a.focus_score), 3)                 AS average_focus,
    countIf(length(a.continuity_flags) > 0)      AS takes_with_problems
FROM the_gate.scenes AS s
LEFT JOIN the_gate.takes         AS t  ON t.scene_id = s.scene_id
LEFT JOIN the_gate.take_analysis AS a  ON a.take_id = t.take_id
LEFT JOIN the_gate.take_characters AS tc
       ON tc.take_id = t.take_id AND tc.prominence = 'foreground'
GROUP BY s.scene_id, place, inside_or_outside, time_of_day, s.scene_type;

-- Every take, flattened, in the words a script supervisor would use.
CREATE OR REPLACE VIEW the_gate_marts.take_log
DEFINER = default SQL SECURITY DEFINER AS
SELECT
    t.scene_id      AS scene_id,
    t.setup_id      AS camera_position,
    t.take_id       AS take_id,
    t.take_no       AS take_number,
    t.duration_s    AS seconds,
    t.circled       AS director_preferred,
    a.shot_size     AS shot_size,
    multiIf(a.shot_size IN ('ELS','LS','MLS'), 'wide',
            a.shot_size = 'MS',                'medium',
            a.shot_size IN ('MCU','CU','ECU'), 'close',
            'other')                            AS framing,
    a.movement      AS camera_movement,
    a.screen_direction                          AS facing,
    a.focus_score   AS focus,
    a.exposure_score                            AS exposure,
    a.continuity_flags                          AS problems,
    length(a.continuity_flags) > 0              AS has_problem
FROM the_gate.takes AS t
INNER JOIN the_gate.take_analysis AS a ON a.take_id = t.take_id;

-- How long each DP takes, by the conditions they were shooting in.
-- This is what the day simulation samples from.
CREATE OR REPLACE VIEW the_gate_marts.dp_pace
DEFINER = default SQL SECURITY DEFINER AS
SELECT
    dp_id                                                    AS dp_id,
    if(int_ext = 'INT', 'inside', 'outside')                 AS inside_or_outside,
    lower(day_night)                                         AS time_of_day,
    scene_type                                               AS scene_type,
    countMerge(n)                                            AS setups_observed,
    round(quantilesTDigestMerge(0.5)(durations)[1] / 60)     AS typical_minutes,
    round(quantilesTDigestMerge(0.9)(durations)[1] / 60)     AS slow_day_minutes
FROM the_gate.setup_duration_stats
GROUP BY dp_id, inside_or_outside, time_of_day, scene_type;

-- Who appears where, and in what framings — the coverage question, per person.
CREATE OR REPLACE VIEW the_gate_marts.person_coverage
DEFINER = default SQL SECURITY DEFINER AS
SELECT
    tc.scene_id                                  AS scene_id,
    c.name                                       AS person,
    c.character_id                               AS character_id,
    multiIf(a.shot_size IN ('ELS','LS','MLS'), 'wide',
            a.shot_size = 'MS',                'medium',
            a.shot_size IN ('MCU','CU','ECU'), 'close',
            'other')                             AS framing,
    count()                                      AS takes,
    groupArray(tc.take_id)                       AS take_ids
FROM the_gate.take_characters AS tc
INNER JOIN the_gate.characters    AS c FINAL ON c.character_id = tc.character_id
INNER JOIN the_gate.take_analysis AS a ON a.take_id = tc.take_id
WHERE tc.prominence = 'foreground'
GROUP BY scene_id, person, character_id, framing;

-- What the world was doing, for joining against shooting times.
CREATE OR REPLACE VIEW the_gate_marts.world_log
DEFINER = default SQL SECURITY DEFINER AS
SELECT
    replaceAll(location_id, '_', ' ')  AS place,
    ts                                 AS happened_at,
    kind                               AS kind,
    severity                           AS severity,
    summary                            AS summary,
    citation_url                       AS source
FROM the_gate.world_events;
