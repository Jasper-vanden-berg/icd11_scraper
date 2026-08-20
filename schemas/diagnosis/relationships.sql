-- schema
CREATE SCHEMA IF NOT EXISTS diagnosis;


-- create table
CREATE TABLE IF NOT EXISTS diagnosis.diagnosis_relationships (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_diagnosis_id BIGINT NOT NULL,
    to_diagnosis_id BIGINT NOT NULL,
    relationship_type varchar(255) NOT NULL,
    is_required BOOLEAN NOT NULL,
    allow_multiple BOOLEAN NOT NULL,

    -- Add relationships
    CONSTRAINT fk_relationship_from
        FOREIGN KEY (from_diagnosis_id)
        REFERENCES diagnosis.diagnosis(id),

    CONSTRAINT fk_relationship_to
        FOREIGN KEY (to_diagnosis_id)
        REFERENCES diagnosis.diagnosis(id),  

    -- Add unique constraints
    CONSTRAINT uq_diagnosis_relationships
        UNIQUE (from_diagnosis_id, to_diagnosis_id, relationship_type),

    -- Add check constaints
    CONSTRAINT chk_no_self_reference
        CHECK (from_diagnosis_id <> to_diagnosis_id)
);

-- Add index(es)
CREATE INDEX IF NOT EXISTS idx_diagnosis_relationships_from
ON diagnosis.diagnosis_relationships(from_diagnosis_id);

CREATE INDEX IF NOT EXISTS idx_diagnosis_relationships_to
ON diagnosis.diagnosis_relationships(to_diagnosis_id);

CREATE INDEX IF NOT EXISTS idx_diagnosis_relationships_type
ON diagnosis.diagnosis_relationships(relationship_type);