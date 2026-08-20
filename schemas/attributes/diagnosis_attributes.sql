-- schema
CREATE SCHEMA IF NOT EXISTS diagnosis;


-- create table
CREATE TABLE IF NOT EXISTS diagnosis.diagnosis_attributes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diagnosis_id BIGINT NOT NULL,
    attribute_id BIGINT NOT NULL,
    is_required BOOLEAN NOT NULL,

    -- Add relationships
    CONSTRAINT fk_attributes_diagnosis_id
        FOREIGN KEY (diagnosis_id)
        REFERENCES diagnosis.diagnosis(id),

    CONSTRAINT fk_attributes_attribute_id
        FOREIGN KEY (attribute_id)
        REFERENCES diagnosis.attributes(id),  

    -- Add unique constraints
    CONSTRAINT uq_diag_attr
        UNIQUE (diagnosis_id, attribute_id)
);

-- Add index(es)
CREATE INDEX IF NOT EXISTS idx_diag_attr_diagnosis
ON diagnosis.diagnosis_attributes(diagnosis_id);

CREATE INDEX IF NOT EXISTS idx_diag_attr_attribute
ON diagnosis.diagnosis_attributes(attribute_id);