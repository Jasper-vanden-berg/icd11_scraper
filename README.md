# ICD-11 scraper and processing pipeline

What is this repo?:
This repo contains all the code required to build the tables in the "diagnosis" schema of felix.
It does so by scraping the ICD-11 api for all it's available diagnoses, splitting the info into tables, and syncing the tables with the DB

How to use this repo:

1. Setting up the environment
The repo contains an environment.yml file, containing all packages used by the scripts. to create the python environment:

conda env create -f config/environment.yml
conda activate icd_scraper

If this code is run from VS code, the environment will have to be set as its python interpreter.

2. Configuring the pipeline
The repo als contains a config.yml file, which contains variables used in the scripts.
Mainly, the config contains these 2 important variable groups:
"database":     If the target database or its main user changes, the variables under this header need to change with it
"client_id/secret": These 2 variables are created at the ICD itself, and are email specific
                    If the main holder of the email leaves, or the code expires, new ones will need to be created and used here

3. Running the script
Once the conda environment is active, and the config is setup, running the pipeline is as easy as running the scripts in order
python scrape_icd_data.py
python clean_table_data.py
python push_table_data.py

Note if the code is run for the first time, the scraper might run regardless of whether the ICD actually updated.
This won't cause any issues, but will result in the codes running for a few minutes, just to make 0 changes to the DB.

