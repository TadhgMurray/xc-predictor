 Project: xc-predictor
# Author: Tadhg Murray
# Subset: Configuration
# Date: 6/6/2026
# File Title: config.py
# Purpose: Stores database connection credentials. This file is in .gitignore
#          so credentials never get pushed to GitHub. Each machine (local
#          Windows, GCP VM) has its own copy with the same structure.

# Postgres connection config — same values on every machine since they all
# connect to the same GCP Cloud SQL instance.
PG_CONFIG = {
    "host": "8.229.237.165",
    "database": "xc_predictor",
    "user": "scraper",
    "password": "Tigger5959!",
    "port": 5432
}