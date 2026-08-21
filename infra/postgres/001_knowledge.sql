CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rea_repositories (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT NOT NULL,
    scanned_at TIMESTAMPTZ NOT NULL,
    inventory JSONB NOT NULL,
    UNIQUE (name, root)
);

CREATE TABLE IF NOT EXISTS rea_knowledge_facts (
    id BIGSERIAL PRIMARY KEY,
    repository_id BIGINT NOT NULL REFERENCES rea_repositories(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector,
    UNIQUE (repository_id, category, name, value)
);

CREATE TABLE IF NOT EXISTS rea_dependency_edges (
    id BIGSERIAL PRIMARY KEY,
    repository_id BIGINT NOT NULL REFERENCES rea_repositories(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence JSONB NOT NULL,
    UNIQUE (repository_id, source, target, relation)
);

CREATE INDEX IF NOT EXISTS ix_rea_facts_repository_category
    ON rea_knowledge_facts(repository_id, category);
CREATE INDEX IF NOT EXISTS ix_rea_edges_repository_source
    ON rea_dependency_edges(repository_id, source);
CREATE INDEX IF NOT EXISTS ix_rea_edges_repository_target
    ON rea_dependency_edges(repository_id, target);
