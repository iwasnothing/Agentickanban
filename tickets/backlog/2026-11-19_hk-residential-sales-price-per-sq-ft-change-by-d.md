---
id: 2026-11-19_hk-residential-sales-price-per-sq-ft-change-by-d
title: 'HK Residential Sales: Price-per-sq-ft Change by District (Last 2 Years) with Bar Chart Visualization'
status: backlog
assignee_bot: bot-001
github_issue: 9
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhJ5j04
priority: medium
labels: []
created_at: '2026-11-19'
updated_at: '2026-11-19'
---

# Objective
Collect public Hong Kong residential transaction data from the Land Registry EPRC, compute the year-over-year price-per-square-foot change for each district over the most recent 24 months available, rank districts from highest increase to largest decrease, and produce horizontal bar charts visualizing the ranking.

# Requirements
- Fetch transaction records from the Hong Kong Land Registry EPRC open data portal (https://www.landsd.gov.hk/en/open-data.html) for the last 24 months available
- Parse and clean the dataset: keep residential transactions only, extract district, transaction date, price (HKD), and saleable area (sq ft)
- Compute price-per-square-foot for each transaction
- Aggregate to district-level median price-per-sq-ft per month
- Calculate the percentage change in median price-per-sq-ft between the earliest and latest month in the 24-month window for each district
- Rank districts from highest positive change to most negative change
- Generate a horizontal bar chart showing each district's percentage change, colored by direction (green for rise, red for drop), sorted by magnitude
- Export the chart as a high-resolution PNG and the ranked table as CSV
- All code must run in a reproducible Python notebook/script with dependencies declared

# Context
- Requested via the Agentic Kanban web UI (webui-request:6cd0eda934).
- This is the user's high-level description only; Superagent must author the ticket body.
- Data source: Hong Kong Land Registry EPRC (Electronic Property Reference Code) open data
- Time window: Most recent 24 months of available transaction data (as of 2026-11-19, likely up to 2026-09 or 2026-10)
- Districts: Use the 18 standard Hong Kong districts
- Visualization: Horizontal bar chart, districts on Y-axis, % change on X-axis, green/red color coding, sorted descending by change
- Output files: chart.png (1920x1080 min), ranked_districts.csv

# Acceptance Criteria
- [ ] - [ ] Transaction data fetched from official HK Land Registry EPRC source for the last 24 months
- [ ] - [ ] Data cleaning correctly filters residential transactions and extracts required fields
- [ ] - [ ] Price-per-square-foot computed accurately for each transaction
- [ ] - [ ] District-level monthly median price-per-sq-ft calculated correctly
- [ ] - [ ] Year-over-year (24-month) percentage change computed per district
- [ ] - [ ] Districts ranked from highest rise to highest drop
- [ ] - [ ] Horizontal bar chart generated with correct sorting, colors, and labels
- [ ] - [ ] Chart exported as PNG (>=1920x1080) and CSV with district, change_pct columns

# Attachments
- (none)

# Execution Log
## Turn 1 Review (superagent)
- Reviewed at 2026-11-19 00:57
- ## Expansion Log (Superagent)
- **High-level prompt received:** Search public Hong Kong residential sales figures, compute price-per-square-foot change by district over the last two years, sort the districts from the highest rise to the highest drop, and visualize the ranking in bar charts.
- **Ticket fields drafted by Superagent:**
- **Title:** HK Residential Sales: Price-per-sq-ft Change by District (Last 2 Years) with Bar Chart Visualization
- **Objective:** Collect public Hong Kong residential transaction data from the Land Registry EPRC, compute the year-over-year price-per-square-foot change for each district over the most recent 24 months available, rank districts from highest increase to largest decrease, and produce horizontal bar charts visualizing the ranking.
- **Requirements:** 9 specific, testable requirements covering data fetching (HK Land Registry EPRC), parsing/cleaning, price-per-sq-ft computation, district-level monthly median aggregation, 24-month percentage change calculation, ranking, horizontal bar chart generation with green/red color coding, PNG and CSV export, and reproducible Python code.
- **Context:** Includes source (HK Land Registry EPRC open data), time window (most recent 24 months as of 2026-11-19), 18 standard HK districts, visualization specs (horizontal bar chart, sorted, colored), output files (chart.png >=1920x1080, ranked_districts.csv).
- **Acceptance Criteria:** 8 unchecked checkboxes covering each major step from data fetch through chart/CSV export.
- **Intended assignee:** bot-001 (MausBot) — implementation/analysis task requiring Python data processing and visualization.

# Final Review
- (filled when done)
