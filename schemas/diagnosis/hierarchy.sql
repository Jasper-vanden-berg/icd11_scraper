-- schema
CREATE SCHEMA IF NOT EXISTS diagnosis;


-- create table
CREATE TABLE IF NOT EXISTS diagnosis.diagnosis_hierarchy (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ancestor_id BIGINT NOT NULL,
    descendant_id BIGINT NOT NULL,
    depth SMALLINT NOT NULL,

    -- Add relationships
    CONSTRAINT fk_relationship_ancestor
        FOREIGN KEY (ancestor_id)
        REFERENCES diagnosis.diagnosis(id),

    CONSTRAINT fk_relationship_descendant
        FOREIGN KEY (descendant_id)
        REFERENCES diagnosis.diagnosis(id),  

    -- Add unique constraints
    CONSTRAINT uq_diag_hierarchy
        UNIQUE (ancestor_id, descendant_id)
);

-- Add index(es)
CREATE INDEX IF NOT EXISTS idx_diagnosis_hierarchy_ancestor
ON diagnosis.diagnosis_hierarchy(ancestor_id);

CREATE INDEX IF NOT EXISTS idx_diagnosis_hierarchy_descendant
ON diagnosis.diagnosis_hierarchy(descendant_id);

CREATE INDEX IF NOT EXISTS idx_diagnosis_hierarchy_depth
ON diagnosis.diagnosis_hierarchy(depth);