---
id: 2026-11-19_hk-residential-sales-price-per-sq-ft-change-by-d-2
title: 'HK Residential Sales: Price-per-sq-ft Change by District (Last 2 Years) with Bar Chart Visualization'
status: backlog
assignee_bot: null
github_issue: 11
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhKJeEI
priority: medium
labels: [data-analysis, hong-kong, real-estate, visualization, public-data]
created_at: '2026-11-19'
updated_at: '2026-11-19'
---

# Objective
Collect public Hong Kong residential sales transaction data, compute year-over-year price-per-square-foot changes for each district over the last two years, rank districts from highest rise to highest drop, and produce a bar chart visualization of the ranking.

# Requirements
- Fetch publicly available Hong Kong residential sales transaction data from official sources (e.g., Hong Kong Land Registry, Rating and Valuation Department, or Centadata) covering the last 24 months.
- Extract district, transaction price, and saleable area (square feet) for each transaction.
- Calculate price per square foot (PSF) for each transaction: transaction_price / saleable_area.
- Aggregate to district-level monthly or quarterly median PSF to reduce outlier impact.
- Compute percentage change in median PSF for each district comparing the most recent period (e.g., last 3 months) vs. the same period 2 years ago.
- Rank all 18 districts from highest positive percentage change (largest rise) to most negative (largest drop).
- Generate a horizontal bar chart visualizing the ranking with districts on Y-axis and percentage change on X-axis, color-coded (green for rise, red for drop).
- Output chart as a high-resolution PNG and/or interactive HTML (Plotly).
- Provide a CSV/JSON summary table with district, period-ago PSF, recent PSF, absolute change, and percentage change.

# Context
- Requested via the Agentic Kanban web UI (webui-request:3e0651de1b).
- This is the user's high-level description only; Superagent must author the ticket body.
- Data sources to consider: Hong Kong Land Registry (登記處) transaction datasets, Rating and Valuation Department (差餉物業估價署) price indices, Centadata/Centaline transaction APIs, or open data portal (data.gov.hk).
- Constraint: Use only publicly available data — no paid API keys or private datasets.
- Hong Kong has 18 districts: Central & Western, Wan Chai, Eastern, Southern, Yau Tsim Mong, Sham Shui Po, Kowloon City, Wong Tai Sin, Kwun Tong, Kwai Tsing, Tsuen Wan, Tuen Mun, Yuen Long, North, Tai Po, Sha Tin, Sai Kung, Islands.
- Time window: Last 24 months from current date (2026-11-19), so approximately Nov 2024 – Oct 2026 vs. Nov 2022 – Oct 2024.
- Visualization should be clear, publication-ready, and accessible (colorblind-friendly palette).

# Acceptance Criteria
- [ ] - [ ] Data source identified and accessible (public API, CSV download, or scrape-able website)
- [ ] - [ ] Raw transaction data for the last 24 months successfully retrieved and stored locally
- [ ] - [ ] Data cleaning handles missing saleable area, outliers, and non-residential property types
- [ ] - [ ] District-level median PSF computed for each month/quarter without errors
- [ ] - [ ] Percentage change calculation correct: ((recent_median - past_median) / past_median) * 100
- [ ] - [ ] All 18 Hong Kong districts present in the final ranking
- [ ] - [ ] Bar chart generated and saved as PNG/HTML with proper labels, title, legend, and color coding
- [ ] - [ ] Summary table exported as CSV and/or JSON with all required fields
- [ ] - [ ] Code is modular, documented, and reproducible (single script or notebook)

# Attachments
- (none)

# Execution Log

# Final Review
- (filled when done)
