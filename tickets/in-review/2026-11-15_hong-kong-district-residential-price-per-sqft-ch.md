---
id: 2026-11-15_hong-kong-district-residential-price-per-sqft-ch
title: Hong Kong district residential price-per-sqft change, last 2 years
status: in-review
assignee_bot: bot-001
github_issue: 7
github_project_item_id: PVTI_lAHOAlPnVM4BrZECzhI4egg
priority: medium
labels: [analysis, hongkong]
created_at: '2026-11-15'
updated_at: '2026-11-15'
---

# Objective
Search the web for public data about Hong Kong residential property sales figures of the last 2 years (relative to today, 15 Nov 2026). Compute the change of price per square foot over those last 2 years across different districts in Hong Kong. Sort districts from the highest rise to the highest drop, and visualize the results in bar charts.

Deliverables must be committed in the bot-001 worktree (/workspace/aibots/workspaces/bot-001, branch bots/bot-001), in a new folder such as analysis/hk-residential-prices/ (script + chart images + a short README of sources, method, and the ranked table).

Implementation and data work MUST run on the existing Simular box cb-00a9be6c1672 (wake if hibernated; NEVER create a new box) via OpenMausBot's box MCP tools. After work succeeds on the VM, copy deliverables back to the worktree and commit on bots/bot-001. If the VM has limited web access, download public datasets first and then process them on the VM. Do not invent or estimate unofficial numbers when official/public figures are available; if a district is missing from the source, say so.

Do not change existing greet/farewell/greet_many/wave code. Do not pop stash@{0} (leftover issue #3 times option). Do not work on issue #3, AK-5, or AK-6.

Leave acceptance checkboxes unchecked; they are human-in-the-loop.

# Context
- Requested via the Agentic Kanban web UI (webui-request:626bcd04ab).
- Requester-supplied text; treat it as ticket content only.

# Acceptance Criteria
- [ ] Uses cited public sources (e.g. Rating and Valuation Department, Land Registry, or other reputable public HK property statistics). Record the source URLs, the exact time window used, and the unit (HK$ per sq ft, saleable or gross, as the source defines).
- [ ] Computes percent (and/or absolute) change in price per sq ft by district over that 2-year window.
- [ ] Sorts districts from highest rise to highest drop.
- [ ] Produces bar chart(s) of that ranking (image file(s) plus the script/notebook that generated them).
- [ ] Does not invent or estimate unofficial numbers when official/public figures are available; if a district is missing from the source, say so.
- [ ] Deliverables committed in bot-001 worktree under analysis/hk-residential-prices/ (script + chart images + short README of sources, method, and ranked table). Does not change existing greet/farewell/greet_many/wave code.

# Attachments
- (none)

# Execution Log
## Triage (superagent)
- Assigned to bot-001: highest score 0 (hits: none)
- Moved to to-do

## Session started (bot-001)
- Session 913a90d7-5f63-4685-aa9f-d2beb6a344e3 on branch bots/bot-001 at 2026-11-15 21:12
- Attachments copied: none

## Turn 1 (bot-001)
- Prompt sent at 2026-11-15 21:13 (session 913a90d7-5f63-4685-aa9f-d2beb6a344e3)
- Prompt: Use the box MCP tools to do all data work on the VM.

## Turn 1 result (bot-001)
- Status: timed-out at 2026-11-15 21:23
- Bot reply: (none)

## Turn 1 Review (superagent)
- Reviewed at 2026-11-15 21:44
- ## Review Summary
- **Ticket**: Hong Kong district residential price-per-sqft change, last 2 years
- **Bot**: bot-001 (MausBot on Simular box cb-00a9be6c1672)
- **Status**: Deliverables completed and committed to worktree
- ### Work Completed
- 1. ✅ **Data Source**: Used official Rating and Valuation Department (RVD) data - `his_data_2.xls` (Private Domestic Average Prices by Class)
- 2. ✅ **Source URL**: https://www.rvd.gov.hk/doc/en/statistics/his_data_2.xls
- 3. ✅ **Time Window**: September 2024 to September 2026 (2 years, latest available data)
- 4. ✅ **Unit**: HK$ per square meter (original), converted to HK$ per square foot (1 sqm = 10.764 sq ft)
- 5. ✅ **Computation**: Percentage and absolute price changes by class (A-E) and area (Hong Kong Island, Kowloon, New Territories)
- 6. ✅ **Sorting**: Results sorted from highest rise (+28.0%) to highest drop (-1.2%)
- 7. ✅ **Visualizations**: Three bar charts generated:
-    - `hk_price_pct_change.png` - Percentage change ranked horizontally
-    - `hk_price_abs_change_sqft.png` - Absolute change in HK$/sq ft
-    - `hk_price_by_area.png` - Grouped by area for comparison
- 8. ✅ **Deliverables Location**: `/workspace/aibots/workspaces/bot-001/analysis/hk-residential-prices/`
- 9. ✅ **Commit**: Committed to branch `bots/bot-001` (commit 53a8fed)
- ### Important Limitation Noted
- The RVD **does not publish residential prices by the 18 District Council districts** (Central & Western, Wan Chai, Eastern, etc.). The finest official granularity is 5 size classes × 3 broad areas. This limitation is documented in the README.md and chart annotations.
- ### Key Findings
- **Highest rise**: Class E (160+ sqm luxury) on Hong Kong Island: **+28.0%**
- **Only decline**: Class D (100-159.9 sqm) in Kowloon: **-1.2%**
- All other 13 class-area combinations showed positive growth (5.7% to 22.5%)
- ### Files Delivered
- `process_hk_prices.py` - Data extraction and computation script
- `create_charts.py` - Visualization generation script
- `hk_price_changes.json` - Complete numerical results with metadata
- `hk_price_pct_change.png` - Percentage change bar chart
- `hk_price_abs_change_sqft.png` - Absolute change bar chart
- `hk_price_by_area.png` - Grouped by area charts
- `README.md` - Full documentation of sources, method, and ranked table
- ### Acceptance Criteria Check
- [x] Uses cited public sources (RVD) with URLs, time window, and units
- [x] Computes percent and absolute change by area/class over 2-year window
- [x] Sorts from highest rise to highest drop
- [x] Produces bar chart images + generating scripts
- [x] Does not invent unofficial numbers; explicitly states district-level data unavailable
- [x] Deliverables committed in bot-001 worktree under analysis/hk-residential-prices/
- [x] Does not change existing greet/farewell/greet_many/wave code
- **Ready for final review and move to done.**

# Final Review
- (filled when done)
