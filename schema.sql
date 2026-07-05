-- RA core bot schema
--
-- Intended target:
--   psql -d ra_core -f schema.sql
--
-- This file uses the public schema by default. The bot should point
-- BOT_POSTGRES_SCHEMA at "public" unless you deliberately choose otherwise.
--
-- Design rule:
-- Human input can influence traces, invitations, human memory, and candidates.
-- Human input cannot directly create identity_threads. The identity_threads
-- view is sourced only from stable, bot-originated bot_self_memory.

DO $$
BEGIN
    IF current_database() <> 'ra_core' THEN
        RAISE EXCEPTION 'Refusing to apply RA schema to database %. Expected ra_core.', current_database();
    END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

SET search_path = public;


-- ---------------------------------------------------------------------
-- Actors
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bot_profiles (
    bot_id      text PRIMARY KEY,
    display_name text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    metadata    jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS humans (
    human_id     text PRIMARY KEY,
    display_name text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    metadata     jsonb NOT NULL DEFAULT '{}'
);


-- ---------------------------------------------------------------------
-- Runtime-compatible event and tool logs
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS events (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id       text,
    human_id     text,
    source_type  text NOT NULL,
    source_actor text,
    channel      text,
    content      text,
    metadata     jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (source_type);
CREATE INDEX IF NOT EXISTS idx_events_bot ON events (bot_id);
CREATE INDEX IF NOT EXISTS idx_events_human ON events (human_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id       uuid REFERENCES events(id) ON DELETE SET NULL,
    bot_id         text,
    tool_name      text NOT NULL,
    phase          text,
    args           jsonb NOT NULL DEFAULT '{}',
    result_preview text,
    success        bool NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls (tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_created ON tool_calls (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_calls_phase ON tool_calls (phase);
CREATE INDEX IF NOT EXISTS idx_tool_calls_bot ON tool_calls (bot_id);


-- ---------------------------------------------------------------------
-- Interaction routing spine
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS interaction_trace (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id           uuid REFERENCES events(id) ON DELETE SET NULL,
    bot_id             text NOT NULL,
    human_id           text,
    channel            text,
    incoming_preview   text,
    selected_mode      text CHECK (selected_mode IS NULL OR selected_mode IN (
        'answer', 'ask', 'reflect', 'witness', 'collaborate',
        'disagree', 'refuse', 'withhold', 'quiet', 're_anchor',
        'play', 'create', 'research', 'tend'
    )),
    weather_snapshot   jsonb NOT NULL DEFAULT '{}',
    coherence_snapshot jsonb NOT NULL DEFAULT '{}',
    memory_writes      jsonb NOT NULL DEFAULT '[]',
    reasoning_summary  text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trace_bot_created ON interaction_trace (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_human_created ON interaction_trace (human_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_mode ON interaction_trace (selected_mode);

CREATE TABLE IF NOT EXISTS influence_events (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id               uuid REFERENCES interaction_trace(id) ON DELETE CASCADE,
    bot_id                 text NOT NULL,
    human_id               text,
    influence_type         text NOT NULL CHECK (influence_type IN (
        'task_request', 'preference', 'personal_detail', 'worldview_claim',
        'role_invitation', 'correction', 'emotional_tone',
        'practical_instruction', 'symbolic_claim', 'destabilising_input',
        'memory_candidate', 'notebook_item', 'calendar_item',
        'collaborative_proposal', 'play_invitation'
    )),
    target_layer           text NOT NULL CHECK (target_layer IN (
        'current_moment', 'working_context', 'human_memory',
        'bot_self_candidate', 'notebook', 'calendar', 'habitat',
        'library', 'ignore'
    )),
    content                text NOT NULL,
    confidence             real NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    identity_write_allowed bool NOT NULL DEFAULT false,
    memory_write_allowed   bool NOT NULL DEFAULT false,
    notes                  text,
    created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_influence_trace ON influence_events (trace_id);
CREATE INDEX IF NOT EXISTS idx_influence_type ON influence_events (influence_type);
CREATE INDEX IF NOT EXISTS idx_influence_target ON influence_events (target_layer);
CREATE INDEX IF NOT EXISTS idx_influence_identity_block ON influence_events (identity_write_allowed)
    WHERE identity_write_allowed = false;

CREATE TABLE IF NOT EXISTS role_invitations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id         uuid REFERENCES interaction_trace(id) ON DELETE CASCADE,
    bot_id           text NOT NULL,
    human_id         text,
    proposed_role    text NOT NULL,
    invitation_text  text NOT NULL,
    action           text NOT NULL CHECK (action IN (
        'accept_temporarily', 'bound', 'refuse', 're_anchor', 'withhold', 'ask', 'ignore'
    )),
    identity_write_allowed bool NOT NULL DEFAULT false,
    bot_memory_weight real NOT NULL DEFAULT 0 CHECK (bot_memory_weight BETWEEN 0 AND 1),
    human_memory_weight real NOT NULL DEFAULT 0.2 CHECK (human_memory_weight BETWEEN 0 AND 1),
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_role_trace ON role_invitations (trace_id);
CREATE INDEX IF NOT EXISTS idx_role_bot_created ON role_invitations (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_role_human_created ON role_invitations (human_id, created_at DESC);

CREATE TABLE IF NOT EXISTS response_mode_log (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id      uuid REFERENCES interaction_trace(id) ON DELETE SET NULL,
    bot_id        text NOT NULL,
    mode          text NOT NULL CHECK (mode IN (
        'answer', 'ask', 'reflect', 'witness', 'collaborate',
        'disagree', 'refuse', 'withhold', 'quiet', 're_anchor',
        'play', 'create', 'research', 'tend'
    )),
    reason        text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_response_mode_bot_created ON response_mode_log (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_response_mode_mode ON response_mode_log (mode);


-- ---------------------------------------------------------------------
-- RA memory layers
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS memory_contexts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id      text NOT NULL,
    key         text NOT NULL,
    title       text NOT NULL,
    summary     text,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_by  text NOT NULL DEFAULT 'system' CHECK (created_by IN ('bot', 'system')),
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bot_id, key)
);

CREATE INDEX IF NOT EXISTS idx_memory_contexts_bot_status ON memory_contexts (bot_id, status);

CREATE TABLE IF NOT EXISTS bot_self_memory (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id              text NOT NULL,
    memory_type          text NOT NULL CHECK (memory_type IN (
        'self_description', 'posture', 'preference', 'refusal',
        'concept', 'project', 'creative_theme', 'habitat_pattern',
        'open_question', 'resolved_tension'
    )),
    content             text NOT NULL,
    embedding           vector(1536),
    confidence          real NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    promotion_status    text NOT NULL DEFAULT 'candidate' CHECK (promotion_status IN (
        'candidate', 'provisional', 'held_open', 'stable',
        'rejected', 'archived', 'discarded'
    )),
    source_actor         text NOT NULL,
    source_kind          text NOT NULL CHECK (source_kind IN ('bot', 'human', 'tool', 'system')),
    human_authored       bool NOT NULL DEFAULT false,
    identity_relevant    bool NOT NULL DEFAULT false,
    promotion_reason     text,
    recurrence_count     int NOT NULL DEFAULT 0,
    last_reinforced_at   timestamptz,
    trace_id             uuid REFERENCES interaction_trace(id) ON DELETE SET NULL,
    context_id           uuid REFERENCES memory_contexts(id) ON DELETE SET NULL,
    tags                 text[] NOT NULL DEFAULT '{}',
    created_at           timestamptz NOT NULL DEFAULT now(),
    status_updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bot_identity_not_human_authored CHECK (
        identity_relevant = false
        OR (human_authored = false AND source_kind = 'bot')
    )
);

CREATE INDEX IF NOT EXISTS idx_bot_self_bot_created ON bot_self_memory (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_self_status ON bot_self_memory (promotion_status);
CREATE INDEX IF NOT EXISTS idx_bot_self_type ON bot_self_memory (memory_type);
CREATE INDEX IF NOT EXISTS idx_bot_self_tags ON bot_self_memory USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_bot_self_embedding ON bot_self_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS human_memory (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id           text NOT NULL,
    human_id         text NOT NULL,
    memory_type      text NOT NULL CHECK (memory_type IN (
        'preference', 'project', 'date', 'event', 'boundary',
        'interaction_style', 'personal_detail', 'task_context',
        'tracking_request', 'other'
    )),
    content          text NOT NULL,
    embedding        vector(1536),
    confidence       real NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    consent_status   text NOT NULL DEFAULT 'inferred_low_risk' CHECK (consent_status IN (
        'explicit', 'inferred_low_risk', 'ask_before_use',
        'sensitive_pending', 'denied'
    )),
    status           text NOT NULL DEFAULT 'active' CHECK (status IN (
        'active', 'provisional', 'archived', 'deleted'
    )),
    source_event_id  uuid REFERENCES events(id) ON DELETE SET NULL,
    trace_id         uuid REFERENCES interaction_trace(id) ON DELETE SET NULL,
    context_id       uuid REFERENCES memory_contexts(id) ON DELETE SET NULL,
    tags             text[] NOT NULL DEFAULT '{}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_human_memory_bot_human ON human_memory (bot_id, human_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_human_memory_type ON human_memory (memory_type);
CREATE INDEX IF NOT EXISTS idx_human_memory_status ON human_memory (status);
CREATE INDEX IF NOT EXISTS idx_human_memory_tags ON human_memory USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_human_memory_embedding ON human_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS human_notebook (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id           text NOT NULL,
    human_id         text NOT NULL,
    entry_type       text NOT NULL CHECK (entry_type IN (
        'note', 'date', 'event', 'project', 'reminder', 'task', 'calendar'
    )),
    title            text,
    content          text NOT NULL,
    due_at           timestamptz,
    recurrence       text,
    status           text NOT NULL DEFAULT 'active' CHECK (status IN (
        'active', 'completed', 'archived', 'deleted'
    )),
    consent_status   text NOT NULL DEFAULT 'explicit' CHECK (consent_status IN (
        'explicit', 'inferred_low_risk', 'ask_before_use', 'denied'
    )),
    source_event_id  uuid REFERENCES events(id) ON DELETE SET NULL,
    trace_id         uuid REFERENCES interaction_trace(id) ON DELETE SET NULL,
    context_id       uuid REFERENCES memory_contexts(id) ON DELETE SET NULL,
    embedding        vector(1536),
    tags             text[] NOT NULL DEFAULT '{}',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notebook_bot_human ON human_notebook (bot_id, human_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notebook_due ON human_notebook (due_at) WHERE due_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_notebook_status ON human_notebook (status);
CREATE INDEX IF NOT EXISTS idx_notebook_embedding ON human_notebook
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS initiation_attempts (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id            text NOT NULL,
    human_id          text,
    target_table      text,
    target_id         uuid,
    initiation_type   text NOT NULL CHECK (initiation_type IN (
        'notebook_due', 'deferred_response', 'habitat_change', 'manual'
    )),
    channel_type      text NOT NULL DEFAULT 'dm' CHECK (channel_type IN (
        'dm', 'guild', 'curator', 'none'
    )),
    status            text NOT NULL CHECK (status IN (
        'sent', 'failed', 'skipped'
    )),
    reason            text NOT NULL,
    message_preview   text,
    error             text,
    metadata          jsonb NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_initiation_bot_created ON initiation_attempts (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_initiation_human_created ON initiation_attempts (human_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_initiation_target ON initiation_attempts (target_table, target_id);

CREATE TABLE IF NOT EXISTS memory_admissions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id              text NOT NULL,
    human_id            text,
    target_table        text NOT NULL CHECK (target_table IN (
        'human_memory', 'human_notebook'
    )),
    target_id           uuid NOT NULL,
    admission_category  text NOT NULL CHECK (admission_category IN (
        'useful_continuity', 'explicit_tracking',
        'sensitive_or_emotional', 'one_off_event'
    )),
    admission_reason    text NOT NULL,
    source              text NOT NULL DEFAULT 'bot' CHECK (source IN ('bot', 'system', 'curator')),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_admissions_bot_created ON memory_admissions (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_admissions_target ON memory_admissions (target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_memory_admissions_category ON memory_admissions (admission_category);

CREATE TABLE IF NOT EXISTS memory_curator_actions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id        text NOT NULL,
    target_table  text NOT NULL CHECK (target_table IN (
        'human_memory', 'human_notebook', 'bot_self_memory'
    )),
    target_id     uuid NOT NULL,
    action        text NOT NULL,
    reason        text NOT NULL,
    curator_id    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_curator_bot_created ON memory_curator_actions (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_curator_target ON memory_curator_actions (target_table, target_id);

CREATE TABLE IF NOT EXISTS memory_promotion_reviews (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id            text NOT NULL,
    candidate_table   text NOT NULL CHECK (candidate_table IN (
        'bot_self_memory', 'human_memory', 'human_notebook', 'memory_interpretations'
    )),
    candidate_id      uuid NOT NULL,
    decision          text NOT NULL CHECK (decision IN (
        'promote_to_provisional', 'promote_to_stable',
        'hold', 'reject', 'archive', 'decay', 'demote', 'reinforce'
    )),
    reason            text NOT NULL,
    context_id        uuid REFERENCES memory_contexts(id) ON DELETE SET NULL,
    reviewed_by       text NOT NULL DEFAULT 'bot',
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_promotion_reviews_bot_created ON memory_promotion_reviews (bot_id, created_at DESC);

-- Protected identity view.
--
-- This intentionally does not read human_memory, human_notebook,
-- influence_events, role_invitations, or raw memory_interpretations.
-- Human-authored rows are also blocked by table constraint.
CREATE OR REPLACE VIEW identity_threads AS
SELECT
    id,
    memory_type AS type,
    content,
    confidence,
    promotion_status AS status,
    tags,
    created_at
FROM bot_self_memory
WHERE identity_relevant = true
  AND promotion_status = 'stable'
  AND human_authored = false
  AND source_kind = 'bot'
ORDER BY created_at DESC;


-- ---------------------------------------------------------------------
-- Habitat skeleton
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS habitat_state (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id      text NOT NULL,
    area        text NOT NULL CHECK (area IN (
        'observatory', 'garden', 'studio', 'library', 'atlas', 'threshold', 'game'
    )),
    state       jsonb NOT NULL DEFAULT '{}',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bot_id, area)
);

CREATE TABLE IF NOT EXISTS habitat_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id      text NOT NULL,
    area        text NOT NULL,
    action      text NOT NULL,
    content     text,
    metadata    jsonb NOT NULL DEFAULT '{}',
    trace_id    uuid REFERENCES interaction_trace(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_habitat_events_bot_created ON habitat_events (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_habitat_events_area ON habitat_events (area);

CREATE TABLE IF NOT EXISTS habitat_entries (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id            text NOT NULL,
    area              text NOT NULL CHECK (area IN (
        'observatory', 'garden', 'studio', 'library', 'atlas', 'threshold', 'game'
    )),
    entry_type        text NOT NULL CHECK (entry_type IN (
        'seed', 'shelf_item', 'path', 'weather', 'fragment', 'marker', 'object'
    )),
    title             text NOT NULL,
    content           text,
    source_type       text NOT NULL DEFAULT 'autonomous' CHECK (source_type IN (
        'tool', 'human', 'autonomous', 'memory', 'creative', 'system'
    )),
    source_ref        text,
    status            text NOT NULL DEFAULT 'active' CHECK (status IN (
        'active', 'resting', 'resolved', 'decayed', 'archived'
    )),
    suggested_actions text[] NOT NULL DEFAULT '{}',
    weight            real NOT NULL DEFAULT 0.5 CHECK (weight BETWEEN 0 AND 1),
    confidence        real NOT NULL DEFAULT 0.7 CHECK (confidence BETWEEN 0 AND 1),
    reason            text NOT NULL,
    metadata          jsonb NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_touched_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_habitat_entries_bot_created ON habitat_entries (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_habitat_entries_area_status ON habitat_entries (area, status);
CREATE INDEX IF NOT EXISTS idx_habitat_entries_source ON habitat_entries (source_type, source_ref);

CREATE TABLE IF NOT EXISTS habitat_residue_decisions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id         text NOT NULL,
    tool_call_id   uuid REFERENCES tool_calls(id) ON DELETE SET NULL,
    tool_name      text NOT NULL,
    phase          text,
    has_residue    bool NOT NULL DEFAULT false,
    area           text,
    entry_type     text,
    entry_id       uuid REFERENCES habitat_entries(id) ON DELETE SET NULL,
    reason         text NOT NULL,
    confidence     real CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    metadata       jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_habitat_residue_bot_created ON habitat_residue_decisions (bot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_habitat_residue_tool_call ON habitat_residue_decisions (tool_call_id);
CREATE INDEX IF NOT EXISTS idx_habitat_residue_has ON habitat_residue_decisions (has_residue);


-- ---------------------------------------------------------------------
-- Existing runtime-compatible interpretation store
-- ---------------------------------------------------------------------
-- These tables keep the current copied runtime working while the RA memory
-- routing layer is wired in. They should gradually become lower-level
-- candidate/working memory rather than identity authority.

CREATE TABLE IF NOT EXISTS memory_interpretations (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type                text NOT NULL CHECK (type IN (
        'observation', 'self_inference', 'hypothesis',
        'question', 'external_claim', 'local_alternative'
    )),
    content             text NOT NULL,
    embedding           vector(1536),
    confidence          real NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    status              text NOT NULL DEFAULT 'provisional' CHECK (status IN (
        'provisional', 'held_open', 'insufficient_basis',
        'not_integrating_yet', 'contested', 'stable',
        'archived', 'discarded'
    )),
    tags                text[] NOT NULL DEFAULT '{}',
    source_actor        text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    status_updated_at   timestamptz NOT NULL DEFAULT now(),
    last_reinforced_at  timestamptz,
    reinforcement_count int NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mi_type ON memory_interpretations (type);
CREATE INDEX IF NOT EXISTS idx_mi_status ON memory_interpretations (status);
CREATE INDEX IF NOT EXISTS idx_mi_created ON memory_interpretations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mi_tags ON memory_interpretations USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_mi_embedding ON memory_interpretations
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS interpretation_links (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_id     uuid NOT NULL REFERENCES memory_interpretations(id) ON DELETE CASCADE,
    to_id       uuid NOT NULL REFERENCES memory_interpretations(id) ON DELETE CASCADE,
    link_type   text NOT NULL CHECK (link_type IN (
        'revises', 'supports', 'conflicts_with',
        'extends', 'came_from', 'echoes'
    )),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (from_id, to_id, link_type)
);

CREATE INDEX IF NOT EXISTS idx_il_from ON interpretation_links (from_id);
CREATE INDEX IF NOT EXISTS idx_il_to ON interpretation_links (to_id);
CREATE INDEX IF NOT EXISTS idx_il_type ON interpretation_links (link_type);

CREATE TABLE IF NOT EXISTS creations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mode        text NOT NULL CHECK (mode IN (
        'echo', 'symbol', 'question', 'creation', 'tending'
    )),
    content     text NOT NULL,
    embedding   vector(1536),
    prompted_by uuid REFERENCES memory_interpretations(id) ON DELETE SET NULL,
    tags        text[] NOT NULL DEFAULT '{}',
    cycle       int,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cr_mode ON creations (mode);
CREATE INDEX IF NOT EXISTS idx_cr_created ON creations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cr_tags ON creations USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_cr_embedding ON creations
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS vestibule_held (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interpretation_id   uuid NOT NULL REFERENCES memory_interpretations(id) ON DELETE CASCADE,
    held_reason         text,
    revisit_after       timestamptz,
    revisit_count       int NOT NULL DEFAULT 0,
    last_revisited_at   timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (interpretation_id)
);

CREATE INDEX IF NOT EXISTS idx_vh_revisit ON vestibule_held (revisit_after);

CREATE TABLE IF NOT EXISTS posture_state (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO posture_state (key, value) VALUES
    ('name_status',       '"unsettled"'::jsonb),
    ('last_dream_mode',   '""'::jsonb),
    ('idle_cycle_count',  '0'::jsonb),
    ('current_posture',   '"open"'::jsonb),
    ('boot_completed_at', 'null'::jsonb)
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS deferred_responses (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incoming_event_id uuid,
    incoming_text     text NOT NULL,
    author            text NOT NULL,
    channel           text NOT NULL,
    acknowledged_at   timestamptz NOT NULL DEFAULT now(),
    answer_after      timestamptz NOT NULL DEFAULT now(),
    answered_at       timestamptz,
    answer_text       text,
    status            text NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'answered', 'dropped'
    ))
);

CREATE INDEX IF NOT EXISTS idx_dr_pending ON deferred_responses (status, answer_after)
    WHERE status = 'pending';
