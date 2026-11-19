---
id: 2026-11-19_hk-residential-price-per-sqft-change-by-district
title: HK Residential Price-per-Sqft Change by District (Last 2 Years) with Bar Chart Visualization
status: to-do
assignee_bot: bot-001
github_issue: 10
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhKA5JE
priority: medium
labels: [data-analysis, visualization, hong-kong, real-estate]
created_at: '2026-11-19'
updated_at: '2026-11-19'
---

# Objective
Collect public Hong Kong residential transaction data from the Land Registry, compute price-per-square-foot (PSF) change by district over the most recent 24 months, rank districts from highest rise to highest drop, and produce a horizontal bar chart visualization of the ranking.

# Requirements
- Fetch transaction records from the Hong Kong Land Registry (or equivalent open data portal) covering the last 24 months
- Filter for residential property types only (exclude commercial, industrial, carpark, etc.)
- Extract district, transaction date, sale price, and saleable area for each transaction
- Compute median PSF per district per month (to reduce outlier impact)
- Calculate 24-month PSF change per district: (latest month median PSF - earliest month median PSF) / earliest month median PSF * 100%
- Rank all districts by PSF change percentage descending
- Generate a horizontal bar chart with districts on y-axis and PSF change % on x-axis, colored green for positive / red for negative
- Export chart as PNG and/or interactive HTML
- Provide a CSV summary table with district, earliest PSF, latest PSF, change %, and rank

# Context
- Requested via the Agentic Kanban web UI (webui-request:363067bdbb).
- This is the user's high-level description only; Superagent must author the ticket body.
- Data source: Hong Kong Land Registry EPR (Electronic Property Records) or data.gov.hk open datasets
- Districts: Use the 18 District Council districts as standard geographic units
- Time window: Most recent 24 calendar months available in the dataset at time of execution
- PSF calculation: sale price (HKD) / saleable area (sq ft); 1 sq m = 10.764 sq ft if area given in sq m
- Handle missing/invalid area or price records by exclusion
- Minimum transaction threshold per district per month: ≥5 transactions for median to be valid

# Acceptance Criteria
- [ ] - [ ] Transaction data successfully fetched from official/public HK source covering ≥22 of the last 24 months
- [ ] - [ ] Residential-only filter correctly excludes non-residential transactions
- [ ] - [ ] Median PSF per district per month computed with ≥5 transaction minimum
- [ ] - [ ] 24-month PSF change % calculated for each of the 18 districts
- [ ] - [ ] Districts ranked from highest rise to highest drop (descending change %)
- [ ] - [ ] Horizontal bar chart generated: districts on y-axis, PSF change % on x-axis, green/red coloring
- [ ] - [ ] Chart exported as PNG (min 1200px width) and interactive HTML (Plotly/Altair)
- [ ] - [ ] CSV summary produced with columns: district, earliest_month, earliest_psf, latest_month, latest_psf, change_pct, rank
- [ ] - [ ] Code is reproducible: single script/notebook with documented dependencies
- [ ] - [ ] Execution log shows data freshness (latest transaction date in dataset) and any districts excluded due to insufficient data

# Attachments
- (none)

# Execution Log
## Turn 1 Review (superagent)
- Reviewed at 2026-11-19 09:44
- ## Expansion Log (Superagent)
- **High-level prompt received:**
- Working title: "Search public Hong Kong residential sales figures, compute price-per-square-foot change by district over the last two"
- High-level description: "Search public Hong Kong residential sales figures, compute price-per-square-foot change by district over the last two years, sort the districts from the highest rise to the highest drop, and visualize the ranking in bar charts."
- Suggested priority: medium
- Context markers: "Requested via the Agentic Kanban web UI (webui-request:363067bdbb).", "This is the user's high-level description only; Superagent must author the ticket body."
- **Fields authored by Superagent:**
- **Title:** HK Residential Price-per-Sqft Change by District (Last 2 Years) with Bar Chart Visualization
- **Objective:** Collect public Hong Kong residential transaction data from the Land Registry, compute price-per-square-foot (PSF) change by district over the most recent 24 months, rank districts from highest rise to highest drop, and produce a horizontal bar chart visualization of the ranking.
- **Requirements:** 10 specific, testable requirements covering data fetching, filtering, computation, ranking, visualization, and export.
- **Context:** 8 items including data source (HK Land Registry EPR / data.gov.hk), district definition (18 District Council districts), time window (most recent 24 calendar months), PSF formula, handling of missing data, and minimum transaction threshold (≥5/month/district).
- **Acceptance Criteria:** 10 concrete unchecked checkboxes covering data completeness, filtering correctness, computation validity, chart specs, export formats, reproducibility, and execution logging.
- **Labels:** data-analysis, visualization, hong-kong, real-estate
- **Priority:** medium
- **Initial status:** backlog
- **Intended assignee:** bot-001 (MausBot) — implementation/analysis task requiring data fetching, computation, and visualization.

## Triage (superagent)
- Assigned to bot-001: highest score 0 (hits: none)
- Moved to to-do

# Final Review
- (filled when done)
