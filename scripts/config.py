# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Configuration
# Date: 6/6/2026
# File Title: config.py
# Purpose: Stores database connection credentials. This file is in .gitignore
#          so credentials never get pushed to GitHub. Each machine (local
#          Windows, GCP VM) has its own copy with the same structure.

# Postgres connection config — updated 6/18/2026 to use Fly.io instead of GCP Cloud SQL.
# Fly proxy must be running: flyctl proxy 5433:5432 -a xc-predictor-db
PG_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5432,
    "dbname":   "xc_predictor",
    "user":     "postgres",
    "password": "Tigger5959!",
}