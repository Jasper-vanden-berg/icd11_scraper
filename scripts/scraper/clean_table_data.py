import yaml
import sys
import logging
import pandas as pd
from pathlib import Path
from collections import defaultdict,deque

# Set up project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from scripts.utils.utils import get_latest_release, get_token
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def load_tsv(path):
    """
    Very simple function to load the created TSV files.
    The only special part is the keep_default_na=False,na_values=[""] which ensures that empty strings are not converted to NaN values.
    """
    return pd.read_csv(path,sep="\t",header=0,keep_default_na=False,na_values=[""])


def to_csv(df,path,name):
    """
    Another simple function to save a dataframe to a TSV file, creating the directory if it doesn't exist.
    """
    Path(path).mkdir(parents=True, exist_ok=True)
    file = path / name
    df.to_csv(file,sep="\t",index=False)


def make_diagnosis_table(df,out_path):
    """
    For the main tables, we can just take the raw data and select the columns we want, renaming them as needed.
    """
    logging.info(f"Processing Diagnosis Table")
    name = "diagnosis.tsv"
    df = df[["icd_11_id","name","code"]]
    df.columns = ["icd_11_id","name","icd_11_code"]
    to_csv(df,out_path,name)


def make_attributes_table(df,out_path):
    """
    For the main tables, we can just take the raw data and select the columns we want, renaming them as needed.
    """
    logging.info(f"Processing Attributes Table")
    name = "attributes.tsv"
    df = df[["icd_11_id","name","code"]]
    df.columns = ["icd_11_id","name","icd_11_code"]
    to_csv(df,out_path,name)


def make_synonyms_table(df,out_path):
    """
    For the main tables, we can just take the raw data and select the columns we want, renaming them as needed.
    """
    logging.info(f"Processing Synonyms Table")
    name = "synonym.tsv"
    df.columns = ["diagnosis_id","synonym"]
    df["language"] = "en"
    to_csv(df,out_path,name)


def make_relationships_table(df,out_path):
    """
    For the main tables, we can just take the raw data and select the columns we want, renaming them as needed.
    """
    logging.info(f"Processing Relationships Table")
    name="relationships.tsv"
    df = df[["icd_11_id","to_diagnosis_id","type","required","allow_multiple"]]
    df.columns = ["from_diagnosis_id","to_diagnosis_id","relationship_type","is_required","allow_multiple"]
    to_csv(df,out_path,name)


def make_closure_table(df, out_path, name):
    """
    This table needs to be generated from the hierarchical data.
    The closure table is a common way to represent hierarchical data in a relational database.
    It contains all ancestor-descendant pairs, along with the depth of the relationship.
    This results in a larger table, but faster queries for hierarchical data.
    """
    logging.info(f"Processing {name} Closure Table")
    graph = defaultdict(list)
    for p, c in zip(df["parent_id"], df["child_id"]):
        graph[p].append(c)

    closure = []
    nodes = set(df["parent_id"]).union(set(df["child_id"]))

    for start in nodes:
        q = deque([(start, 0)])
        seen = set()
        while q:
            node, depth = q.popleft()
            if node in seen:
                continue
            seen.add(node)
            closure.append((start, node, depth))
            for child in graph.get(node, []):
                if child not in seen:
                    q.append((child, depth + 1))

    closure_df = pd.DataFrame(
        closure,
        columns=["ancestor_id", "descendant_id", "depth"]
    )

    to_csv(closure_df,out_path,name)
    return closure_df


def get_all_children(closure_df, extension_mappings):
    """
    This helper function gets all the children of the start values in the extension mappings.
    It uses the closure table to find all descendants of the start values.
    """
    all_start_values = set()

    for cfg in extension_mappings.values():
        all_start_values.update(cfg["start_values"])

    children = closure_df[
        closure_df["ancestor_id"].isin(all_start_values)
    ]["descendant_id"].unique()

    return set(children)


def build_descendant_to_end_value_map(closure_df, extension_mappings):
    """
    This function uses the extension mappings from the config file, to reduce the amount of unique attributes.
    This is done since the ICD-11 contains many similar / overlapping attributes, which can be simplified without losing information.
    For instance, the ICD-11 contains both a "mild-moderate-severe" and a "mild moderate severe" scale.
    Some diagnoses also have a severity to mild, moderate or severe, without specifying its linked to one of these scales.
    By merging this info, we can severly reduce complexity, and make data easier to compare.
    """
    # 1. flatten start_values → end_value
    start_map = []
    for cfg in extension_mappings.values():
        end_value = cfg["end_value"]
        for start in cfg["start_values"]:
            start_map.append((start, end_value))

    start_df = pd.DataFrame(start_map, columns=["ancestor_id", "end_value"])

    # 2. join with closure (expands to all children)
    merged = closure_df.merge(start_df, on="ancestor_id", how="inner")

    # 3. keep descendant → end_value mapping
    mapping_df = merged[["descendant_id", "end_value"]].drop_duplicates()

    return mapping_df


def make_diagnosis_attributes_table(df, out_path, extension_mapping, closure_df):
    """
    This function runs attributes through the build_descendant function first.
    After, that its again a simple process of filtering and renaming before writing to a new TSV
    """
    logging.info(f"Processing Diagnosis-Attributes Table")
    mapping_df = build_descendant_to_end_value_map(
        closure_df,
        extension_mapping
    )

    df = df.merge(
        mapping_df,
        left_on="attribute_id",
        right_on="descendant_id",
        how="left"
    )

    df = df.drop(columns=["descendant_id"])
    df = df[["icd_11_id", "end_value", "required"]]

    df.columns = ["diagnosis_id", "attribute_id", "is_required"]
    df = df.drop_duplicates()
    to_csv(df, out_path, "diagnosis_attributes.tsv")



def main():
    #Read the config file
    logging.info(f"Start table cleaning")
    config_path = Path(f"{PROJECT_ROOT}/config/config.yml")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    #Get the paramater groups
    api_settings = config["scraper"]["api_settings"]
    scrape_settings = config["scraper"]["scrape_settings"]
    icd_settings = config["scraper"]["icd"]
    
    #Get the icd version
    token = get_token(api_settings)
    icd_version = get_latest_release(
        api_settings,
        token,
    )
    
    #Prep the file paths
    raw_path = scrape_settings["out_dir"].replace("version",icd_version)
    in_path = Path(raw_path) / "raw"
    out_path = Path(raw_path) / "clean"
    
    #Get the diagnosis data, and seperatly extract the diagnosis and extension_codes.    
    diagnosis_table = load_tsv(in_path/"diagnosis.tsv")
    true_diagnosis_table = diagnosis_table[diagnosis_table["type"] == "diagnosis"]
    true_diagnosis_ids = true_diagnosis_table["icd_11_id"]
    true_attribute_table = diagnosis_table[diagnosis_table["type"] == "extension_codes"]
    true_attribute_ids = true_attribute_table["icd_11_id"]
    make_diagnosis_table(true_diagnosis_table,out_path)
    make_attributes_table(true_attribute_table,out_path)

    #Process the synonym table, keeping only entries for which we have the diagnosis id
    synonym_table = load_tsv(in_path/"synonyms.tsv")
    synonym_table["icd_11_id"] = synonym_table["icd_11_id"].astype(str)
    true_synonyms = synonym_table[synonym_table["icd_11_id"].isin(true_diagnosis_ids)]
    make_synonyms_table(true_synonyms,out_path)

    #Process the relationships data, keeping only entries for which we have the diagnosis id
    relationships_table = load_tsv(in_path/"relationships.tsv")
    true_relationships_table = relationships_table[
        relationships_table["icd_11_id"].isin(true_diagnosis_ids) &
        relationships_table["to_diagnosis_id"].isin(true_diagnosis_ids)
    ]
    make_relationships_table(true_relationships_table,out_path)

    #Split the hierarchy data, and turn it into 2 closure tables (diagnosis and attributes)
    hierarchy_table = load_tsv(in_path/"hierarchy.tsv")
    hierarchy_table[["parent_id","child_id"]] = hierarchy_table[["parent_id","child_id"]].astype(str)
    diagnosis_hierarchy_table = hierarchy_table[
        hierarchy_table["parent_id"].isin(true_diagnosis_ids) &
        hierarchy_table["child_id"].isin(true_diagnosis_ids)
    ]
    attributes_hierarchy_table = hierarchy_table[
        hierarchy_table["parent_id"].isin(true_attribute_ids) &
        hierarchy_table["child_id"].isin(true_attribute_ids)   
    ]
    
    diagnosis_closure = make_closure_table(diagnosis_hierarchy_table,out_path,"diagnosis_hierarchy.tsv")
    attribute_closure = make_closure_table(attributes_hierarchy_table,out_path,"attributes_hierarchy.tsv")

    #Make the attribute table, mapping the attributes to the extension mapping first.
    attribute_data_table = load_tsv(in_path/"attributes.tsv")
    extension_mapping = icd_settings["extension_mappings"]
    allowed_extensions = get_all_children(attribute_closure,extension_mapping)
    attribute_data_table[["icd_11_id", "attribute_id"]] = attribute_data_table[["icd_11_id", "attribute_id"]].astype(str)
    true_attribute_table = attribute_data_table[
        attribute_data_table["icd_11_id"].isin(true_diagnosis_ids) &
        attribute_data_table["attribute_id"].isin(allowed_extensions)  
    ]
    make_diagnosis_attributes_table(true_attribute_table,out_path,extension_mapping,attribute_closure)
    logging.info(f"Table Cleaning Finished")
    
if __name__ == "__main__":
    main()