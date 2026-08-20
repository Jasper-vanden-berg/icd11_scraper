import asyncio
import copy
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import aiohttp
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from scripts.utils.utils import get_latest_release, get_token


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_INTERVAL_SECONDS = 3
REQUEST_CONCURRENCY = 100
REQUEST_TIMEOUT_SECONDS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ScraperState:
    def __init__(self):
        self.diagnosis_table = {}
        self.diagnosis_hierarchy_table = defaultdict(list)
        self.diagnosis_synonyms_table = {}
        self.diagnosis_relationships_table = {}
        self.diagnosis_attributes_table = {}


class ProgressLogger:
    """
    Tracks scrape progress using a monotonic clock.

    time.monotonic() is appropriate here because it measures elapsed time
    independently of changes to the system wall clock.
    """

    def __init__(self, interval_seconds=LOG_INTERVAL_SECONDS):
        self.interval_seconds = interval_seconds
        self.last_log = time.monotonic()

    def maybe_log(self, processed_count):
        now = time.monotonic()

        if now - self.last_log >= self.interval_seconds:
            logging.info(
                "Processed %d nodes",
                processed_count,
            )
            self.last_log = now


# ---------------------------------------------------------------------------
# Data parsing
# ---------------------------------------------------------------------------

def url_splitter(url_list):
    """
    Extract node IDs from ICD URLs.

    Supports both MMS and entity URLs.
    """
    if not url_list:
        return []

    if isinstance(url_list, str):
        url_list = [url_list]

    result = []

    for url in url_list:
        if "mms" not in url and "entity" not in url:
            raise ValueError(
                f"Unsupported URL found: {url}. "
                "Only MMS and entity URLs are supported."
            )

        parts = url.rstrip("/").split("/")

        if parts[-1].isdigit():
            node_id = parts[-1]
        else:
            node_id = "/".join(parts[-2:])

        if node_id not in result:
            result.append(node_id)

    return result


def to_bool(value):
    """Convert ICD pseudo-booleans to Python booleans."""
    if value is None:
        return None

    normalized = str(value).strip().lower()

    true_values = {
        "true",
        "allowalways",
        "allowedexceptfromsameblock",
    }

    false_values = {
        "false",
        "notallowed",
    }

    if normalized in true_values:
        return True

    if normalized in false_values:
        return False

    raise ValueError(
        f"No boolean conversion defined for value: {value!r}"
    )


def merge_from_parent(parent, child):
    """
    Recursively merge parent attributes into child attributes.

    Child values take precedence.
    Lists are merged while preserving order and removing duplicates.
    """
    parent = parent or {}
    child = child or {}

    result = copy.deepcopy(child)

    for key, parent_value in parent.items():
        if key not in result:
            result[key] = copy.deepcopy(parent_value)
            continue

        child_value = result[key]

        if isinstance(parent_value, dict) and isinstance(child_value, dict):
            result[key] = merge_from_parent(parent_value, child_value)

        elif isinstance(parent_value, list) and isinstance(child_value, list):
            result[key] = list(
                dict.fromkeys(parent_value + child_value)
            )

    return result


# ---------------------------------------------------------------------------
# Entity processing
# ---------------------------------------------------------------------------

def retrieve_children(data):
    """Retrieve official and index-term children."""
    main_children = url_splitter(
        data.get("child") or []
    )

    index_terms = data.get("indexTerm") or []

    index_term_children = url_splitter(
        item.get("foundationReference")
        for item in index_terms
        if item.get("foundationReference")
    )

    return list(
        dict.fromkeys(main_children + index_term_children)
    )


def retrieve_relationships(data, node_id, children):
    """
    Retrieve attributes and diagnosis-to-diagnosis relationships.
    """
    relationship_keys = {
        "hasManifestation",
        "hasCausingCondition",
        "associatedWith",
    }

    attributes = {}
    relationships = {}

    for group in data.get("postcoordinationScale") or []:
        attribute_type = group.get("axisName", "").rsplit("/", 1)[-1]

        options = [
            option for option in url_splitter(group.get("scaleEntity") or [])
            if option not in children and option != node_id
        ]

        entry = {
            "required": to_bool(group.get("requiredPostcoordination")),
            "allow_multiple": to_bool(group.get("allowMultipleValues", False)),
            "options": options,
        }

        if attribute_type in relationship_keys:
            relationships[attribute_type] = entry
        else:
            attributes[attribute_type] = entry

    return attributes, relationships


def process_entity(data):
    """Process entity API data."""
    children = retrieve_children(data)
    
    synonyms = []
    for synonym in data.get("synonym") or []:
        label = synonym.get("label") or {}
        if label.get("@language") == "en":
            synonyms.append(label.get("@value", ""))

    return children, synonyms


def process_mms(data, node_id):
    """Process MMS API data."""
    children = retrieve_children(data)
    attributes, relationships = retrieve_relationships(data,node_id,children)

    return children, attributes, relationships


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

async def fetch_json(session, url, headers, semaphore):
    """Fetch JSON from an API endpoint."""
    async with semaphore:
        try:
            async with session.get(url,headers=headers) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                return await response.json()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logging.error("Request failed for %s: %s",url,exc,)
            return None

# ---------------------------------------------------------------------------
# async api processor
# ---------------------------------------------------------------------------

async def process_urls(session,urls,headers,node_id,semaphore):
    """
    Fetch and combine entity and MMS data for a node.
    """
    entity_url = urls[1] + node_id
    mms_url = urls[0] + node_id

    #MMS is more extensive, but some diagnosis only have entity APi available. so we fetch both and check.
    entity_data, mms_data = await asyncio.gather(
        fetch_json(session,entity_url,headers,semaphore),
        fetch_json(session,mms_url,headers,semaphore),
    )
    data = mms_data or entity_data
    if not data:
        return None

    title = data.get("title") or {}
    name = title.get("@value", "").replace("\t", " ")

    #By design, we already processed the parent id. Here, we check if the API returns that same ID
    parent_urls = data.get("parent")
    parent_ids = url_splitter(parent_urls)
    parent_id = parent_ids[0] if parent_ids else None

    #There is 1 edge case where the parent id is just the url without an extension. this is its real id
    if parent_id and "mms" in parent_id:
        parent_id = "455013390"

    children = []
    synonyms = []
    attributes = {}
    relationships = {}

    if entity_data:
        children, synonyms = process_entity(entity_data)

    if mms_data:
        (mms_children,attributes,relationships) = process_mms(mms_data,node_id)
        children = list(dict.fromkeys(children + mms_children))

    return {
        "name": name,
        "code": data.get("code"),
        "children": children,
        "synonyms": synonyms,
        "attributes": attributes,
        "relationships": relationships,
        "parent_id": parent_id,
    }


# ---------------------------------------------------------------------------
# Recursive scraper
# ---------------------------------------------------------------------------

async def scrape_tree(session,urls,headers,node_id,state,base_codes,semaphore,
    seen,lock,progress_logger,parent_id=None,diag_type="diagnosis"):
    """Recursively scrape the ICD tree."""
    
    #Get the diagnosis type (diagnosise, symptoms, medication, extension etc.)
    diag_type = base_codes.get(node_id, diag_type)

    diagnosis = await process_urls(session, urls, headers, node_id, semaphore)
    if not diagnosis:
        return

    #Sanity check, if diagnosise A has child diagnosis B and we get to B, check whether its parent is A
    actual_parent_id = diagnosis["parent_id"]
    if parent_id and actual_parent_id != parent_id:
        return

    async with lock:
        #Skip already processed diagnoses.
        if node_id in seen:
            return
        seen.add(node_id)

    state.diagnosis_table[node_id] = {
        "name": diagnosis["name"],
        "code": diagnosis["code"],
        "type": diag_type,
    }

    #Remove children we know we already saw. probably redundant but for sanity.
    children = [
        child_id for child_id in diagnosis["children"]
        if child_id not in seen and child_id not in state.diagnosis_table
    ]

    state.diagnosis_hierarchy_table[actual_parent_id].append(node_id)

    if diagnosis["synonyms"]:
        state.diagnosis_synonyms_table[node_id] = diagnosis["synonyms"]

    parent_attributes = state.diagnosis_attributes_table.get(parent_id, {})
    parent_relationships = state.diagnosis_relationships_table.get(parent_id, {})

    attributes = merge_from_parent(parent_attributes, diagnosis["attributes"])
    relationships = merge_from_parent(parent_relationships, diagnosis["relationships"])

    if attributes:
        state.diagnosis_attributes_table[node_id] = attributes

    if relationships and diag_type == "diagnosis":
        state.diagnosis_relationships_table[node_id] = relationships

    progress_logger.maybe_log(len(state.diagnosis_table))

    await asyncio.gather(*(
        scrape_tree(
            session=session,
            urls=urls,
            headers=headers,
            node_id=child_id,
            state=state,
            base_codes=base_codes,
            semaphore=semaphore,
            seen=seen,
            lock=lock,
            progress_logger=progress_logger,
            parent_id=node_id,
            diag_type=diag_type,
        )
        for child_id in children
    ))


def export_to_tsv(data, output_dir, file_name):
    """Export scraper data to TSV."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if file_name == "diagnosis":
        columns = ["icd_11_id", "name", "code", "type"]
        rows = [[k, v["name"], v["code"], v["type"]] for k, v in data.items()]

    elif file_name == "hierarchy":
        columns = ["parent_id", "child_id"]
        rows = [[p, c] for p, children in data.items() for c in children]

    elif file_name == "synonyms":
        columns = ["icd_11_id", "synonym"]
        rows = [[k, s] for k, synonyms in data.items() for s in synonyms]

    elif file_name in {"attributes", "relationships"}:
        target = "to_diagnosis_id" if file_name == "relationships" else "attribute_id"
        columns = ["icd_11_id", "type", "required", "allow_multiple", target]
        rows = [
            [node_id, attr_type, v["required"], v["allow_multiple"], option]
            for node_id, attrs in data.items()
            for attr_type, v in attrs.items()
            for option in v["options"]
        ]

    else:
        raise ValueError(f"Unsupported export type: {file_name}")

    file_path = output_dir / f"{file_name}.tsv"
    pd.DataFrame(rows, columns=columns).to_csv(file_path, sep="\t", index=False)
    logging.info("Exported %s", file_path)


async def async_main():
    config_path = PROJECT_ROOT / "config" / "config.yml"
    with config_path.open("r") as file:
        config = yaml.safe_load(file)

    api_settings = config["scraper"]["api_settings"]

    token = get_token(api_settings)
    icd_version = get_latest_release(api_settings,token)

    logging.info("Latest ICD-11 version: %s", icd_version)

    # Replace this with the actual version stored in your DB.
    db_version = ""

    if db_version == icd_version:
        logging.info("ICD-11 version is up to date: %s. ""No update needed.",db_version,)
        return

    logging.info("ICD-11 version changed from %s to %s. ""Updating database...",db_version,icd_version,)

    icd_settings = config["scraper"]["icd"]
    scrape_settings = config["scraper"]["scrape_settings"]

    urls = [url.replace("version", icd_version) for url in scrape_settings["urls"]]

    main_ancestor_id = (scrape_settings["main_ancestor_id"])
    base_codes = {value["id"]: key for key, value in icd_settings["base_codes"].items()}

    state = ScraperState()
    semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)
    lock = asyncio.Lock()
    seen = set()
    progress_logger = ProgressLogger(interval_seconds=LOG_INTERVAL_SECONDS)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "en",
        "API-Version": "v2",
    }

    async with aiohttp.ClientSession(timeout=timeout) as session:

        await scrape_tree(
            session=session,
            urls=urls,
            headers=headers,
            node_id=main_ancestor_id,
            state=state,
            base_codes=base_codes,
            semaphore=semaphore,
            seen=seen,
            lock=lock,
            progress_logger=progress_logger,
        )

    out_dir = scrape_settings["out_dir"].replace("version",icd_version)
    output_path = PROJECT_ROOT / out_dir / "raw"

    export_to_tsv(state.diagnosis_table,output_path,"diagnosis")
    export_to_tsv(state.diagnosis_attributes_table,output_path,"attributes")
    export_to_tsv(state.diagnosis_hierarchy_table,output_path,"hierarchy")
    export_to_tsv(state.diagnosis_relationships_table,output_path,"relationships")
    export_to_tsv(state.diagnosis_synonyms_table,output_path,"synonyms")

    logging.info("Scraping complete. Processed %d nodes.",len(state.diagnosis_table))


if __name__ == "__main__":
    asyncio.run(async_main())