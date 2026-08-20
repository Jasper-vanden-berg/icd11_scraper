import yaml
import logging
from pathlib import Path
import pandas as pd
import psycopg2
import sys
from psycopg2 import sql
from psycopg2.extras import execute_values

# Set up project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from scripts.utils.utils import get_latest_release, get_token


def load_config(config_path):
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_connection(db_config):
    return psycopg2.connect(
        host=db_config["host"],
        port=db_config["port"],
        dbname=db_config["dbname"],
        user=db_config["user"],
        password=db_config["password"],
    )


def run_sql_file(conn, path: Path):
    with open(path, "r") as f:
        sql_query = f.read()

    with conn.cursor() as cur:
        cur.execute(sql_query)

    conn.commit()


def load_schemas(conn, schema_root: Path):
    #Here we initiate the tables.
    #Tables who's incremental ID we need for other tables are prioritized.
    priority = [
        schema_root / "attributes/attributes.sql",
        schema_root / "diagnosis/diagnosis.sql",
    ]

    for sql_file in priority:
        logging.info("Applying schema: %s", sql_file)
        run_sql_file(conn, sql_file)

    for sql_file in schema_root.rglob("*.sql"):
        if sql_file in priority:
            continue

        logging.info("Applying schema: %s", sql_file)
        run_sql_file(conn, sql_file)


def load_tsv(path: Path):
    if not path.exists():
        logging.warning("Missing file: %s", path)
        return None

    return pd.read_csv(path, sep="\t")


def build_mapping(conn, table, key_col):
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT {key_col}, id
            FROM {table}
        """)
        rows = cur.fetchall()

    return {str(k).strip(): v for k, v in rows}


def sync_dataframe(
    conn,
    table_name: str,
    df: pd.DataFrame,
    key_columns,
    chunk_size: int = 5000,
):
    """Synchronize a dataframe with a PostgreSQL table."""

    if df is None or df.empty:
        logging.warning("No data for %s", table_name)
        return

    df = df.where(pd.notnull(df), None)

    schema, table = table_name.split(".", 1)
    temp_table = f"tmp_{table}"
    columns = list(df.columns)
    update_columns = [c for c in columns if c not in key_columns]

    try:
        with conn.cursor() as cur:
            _create_temp_table(cur, schema, table, temp_table, columns)
            _load_temp_table(
                cur, conn, temp_table, columns, df, chunk_size
            )

            if table_name == "diagnosis.diagnosis":
                _check_protected_diagnoses(cur, temp_table, columns)
                
            join_condition = sql.SQL(" AND ").join(
                sql.SQL("target.{0} = source.{0}").format(
                    sql.Identifier(c)
                )
                for c in key_columns
            )

            inserted = _insert_rows(
                cur, schema, table, temp_table,
                columns, join_condition
            )

            updated = _update_rows(
                cur, schema, table, temp_table,
                update_columns, join_condition
            )

            deleted = _delete_rows(
                cur, schema, table, temp_table,
                join_condition
            )

        conn.commit()

        logging.info(
            "%s: inserted=%s updated=%s deleted=%s",
            table_name, inserted, updated, deleted,
        )

    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"Sync failed for {table_name}") from exc


def _create_temp_table(cur, schema, table, temp_table, columns):
    cur.execute(
        sql.SQL("""
            CREATE TEMP TABLE {} AS
            SELECT {} FROM {}.{} WITH NO DATA
        """).format(
            sql.Identifier(temp_table),
            sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            sql.Identifier(schema),
            sql.Identifier(table),
        )
    )


def _load_temp_table(cur, conn, temp_table, columns, df, chunk_size):
    values = [
        tuple(x.item() if hasattr(x, "item") else x for x in row)
        for row in df.to_numpy()
    ]

    query = sql.SQL("""
        INSERT INTO {} ({})
        VALUES %s
    """).format(
        sql.Identifier(temp_table),
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
    ).as_string(conn)

    for start in range(0, len(values), chunk_size):
        execute_values(
            cur,
            query,
            values[start:start + chunk_size],
        )


def _check_protected_diagnoses(cur, temp_table, columns):
    """Prevent changes to diagnoses referenced by autopsy_diagnosis."""

    cur.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'autopsy_diagnosis'
        )
    """)

    if not cur.fetchone()[0]:
        return

    cur.execute("""
        SELECT DISTINCT diagnosis_id
        FROM autopsy_diagnosis
        WHERE diagnosis_id IS NOT NULL
    """)

    protected_ids = [row[0] for row in cur.fetchall()]

    if not protected_ids:
        return

    # Find protected diagnoses that are either deleted or changed.
    changed_columns = [
        column for column in columns
        if column != "id"
    ]

    changed_condition = sql.SQL(" OR ").join(
        sql.SQL("target.{0} IS DISTINCT FROM source.{0}").format(
            sql.Identifier(column)
        )
        for column in changed_columns
    )

    cur.execute(
        sql.SQL("""
            SELECT
                target.id,
                target.*,
                source.*
            FROM diagnosis AS target
            LEFT JOIN {temp} AS source
                ON source.id = target.id
            WHERE target.id = ANY(%s)
              AND (
                  source.id IS NULL
                  OR {changed_condition}
              )
        """).format(
            temp=sql.Identifier(temp_table),
            changed_condition=changed_condition,
        ),
        (protected_ids,),
    )

    changes = cur.fetchall()

    if not changes:
        return

    raise ValueError(
        "Cannot modify diagnoses referenced by autopsy_diagnosis:\n"
        + "\n".join(
            f"diagnosis_id={row[0]}: {row}"
            for row in changes
        )
    )
    
def _insert_rows(cur, schema, table, temp_table, columns, join_condition):
    cur.execute(
        sql.SQL("""
            INSERT INTO {schema}.{table} ({columns})
            SELECT {source_columns}
            FROM {temp} AS source
            WHERE NOT EXISTS (
                SELECT 1
                FROM {schema}.{table} AS target
                WHERE {join_condition}
            )
            RETURNING 1
        """).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            source_columns=sql.SQL(", ").join(
                sql.SQL("source.{}").format(sql.Identifier(c))
                for c in columns
            ),
            temp=sql.Identifier(temp_table),
            join_condition=join_condition,
        )
    )
    return len(cur.fetchall())


def _update_rows(
    cur, schema, table, temp_table, update_columns, join_condition
):
    if not update_columns:
        return 0

    changed = sql.SQL(" OR ").join(
        sql.SQL("target.{0} IS DISTINCT FROM source.{0}").format(
            sql.Identifier(c)
        )
        for c in update_columns
    )

    cur.execute(
        sql.SQL("""
            UPDATE {schema}.{table} AS target
            SET {updates}
            FROM {temp} AS source
            WHERE {join_condition}
              AND ({changed})
            RETURNING 1
        """).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            updates=sql.SQL(", ").join(
                sql.SQL("{0} = source.{0}").format(sql.Identifier(c))
                for c in update_columns
            ),
            temp=sql.Identifier(temp_table),
            join_condition=join_condition,
            changed=changed,
        )
    )
    return len(cur.fetchall())


def _delete_rows(cur, schema, table, temp_table, join_condition):
    cur.execute(
        sql.SQL("""
            DELETE FROM {schema}.{table} AS target
            WHERE NOT EXISTS (
                SELECT 1
                FROM {temp} AS source
                WHERE {join_condition}
            )
            RETURNING 1
        """).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table),
            temp=sql.Identifier(temp_table),
            join_condition=join_condition,
        )
    )
    return len(cur.fetchall())


def main():
    logging.basicConfig(level=logging.INFO)

    config = load_config(f"{PROJECT_ROOT}/config/config.yml")
    db_config = config["database"]["postgres"]
    api_settings = config["scraper"]["api_settings"]

    token = get_token(api_settings)
    icd_version = get_latest_release(api_settings, token)

    input_dir = Path(
        config["scraper"]["scrape_settings"]["out_dir"].replace(
            "version",
            icd_version,
        )
    )

    conn = get_connection(db_config)

    # 1. Load database schemas
    schema_root = Path("schemas")
    load_schemas(conn, schema_root)

    # 2. Sync diagnosis and attributes first
    df_diag = load_tsv(input_dir / "clean/diagnosis.tsv")
    df_attr = load_tsv(input_dir / "clean/attributes.tsv")

    sync_dataframe(
        conn,
        "diagnosis.diagnosis",
        df_diag,
        ["icd_11_id"],
    )

    sync_dataframe(
        conn,
        "diagnosis.attributes",
        df_attr,
        ["icd_11_id"],
    )

    # 3. Get mappings of generated database IDs
    diag_map = build_mapping(
        conn,
        "diagnosis.diagnosis",
        "icd_11_id",
    )

    attr_map = build_mapping(
        conn,
        "diagnosis.attributes",
        "icd_11_id",
    )

    # 4. Sync remaining tables
    table_mapping = {
        "diagnosis.diagnosis_hierarchy": (
            "diagnosis_hierarchy",
            ["ancestor_id", "descendant_id"],
        ),
        "diagnosis.diagnosis_synonyms": (
            "synonym",
            ["diagnosis_id", "synonym","language"],
        ),
        "diagnosis.diagnosis_relationships": (
            "relationships",
            ["from_diagnosis_id", "to_diagnosis_id","relationship_type"],
        ),
        "diagnosis.attributes_hierarchy": (
            "attributes_hierarchy",
            ["ancestor_id","descendant_id"],
        ),
        "diagnosis.diagnosis_attributes": (
            "diagnosis_attributes",
            ["diagnosis_id", "attribute_id"],
        ),
    }

    diag_columns = {
        "diagnosis_id",
        "ancestor_id",
        "descendant_id",
        "from_diagnosis_id",
        "to_diagnosis_id",
    }

    for target_table, (target_file, key_columns) in table_mapping.items():
        file = input_dir / "clean" / f"{target_file}.tsv"
        df = load_tsv(file)

        if df is None:
            continue

        for col in df.columns:
            if col == "attribute_id" or (col in diag_columns and target_file == "attributes_hierarchy"):
                df[col] = df[col].astype(str).map(attr_map)
            elif col in diag_columns:
                df[col] = df[col].astype(str).map(diag_map)
            if df[col].isna().any():
                logging.warning(
                    "%s: %s values could not be mapped",
                    target_table,
                    col,
                )
        sync_dataframe(
            conn,
            target_table,
            df,
            key_columns,
        )

    conn.close()


if __name__ == "__main__":
    main()