-- schema
CREATE SCHEMA IF NOT EXISTS diagnosis;


-- create table
CREATE TABLE IF NOT EXISTS diagnosis.diagnosis_synonyms (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diagnosis_id BIGINT NOT NULL,
    synonym VARCHAR(255) NOT NULL,
    language VARCHAR(10) NOT NULL,

    -- Add relationships
    CONSTRAINT fk_relationship_diagnosis_id
        FOREIGN KEY (diagnosis_id)
        REFERENCES diagnosis.diagnosis(id),

    -- Add unique constraints
    CONSTRAINT uq_diagnosis_synonym
        UNIQUE (diagnosis_id, synonym, language)
);

-- Add index(es)
CREATE INDEX IF NOT EXISTS idx_diagnosis_synonyms_diagnosis_id
ON diagnosis.diagnosis_synonyms(diagnosis_id);