---
name: crm
description: Search and retrieve insurance customer profiles, policies, and claims from the CRM database (DuckDB)
enabled: true
---

## Instructions

When the user asks about an insurance customer, policyholder, their policies, or claims, use the CRM tools to retrieve data from the DuckDB insurance database.

The CRM database contains three tables: **customers** (1,000 records), **policies** (~2,500 records across auto/home/travel/life types), and **claims** (~900 records).

### 1. Choose the Right Tool

| User provides | Tool | Example |
|---------------|------|---------|
| A name or partial name | `crm_search_name` | "Look up John Doe", "find customer Smith" |
| A customer ID | `crm_search_id` | "Get customer 42" |
| Request for full policy details | `crm_get_policies` | "Show all policies for customer 42" |
| General overview | `crm_list_customers` | "List insurance customers" |
| Complex analytics / aggregation | `crm_analytics` | "What is the total coverage by policy type?", "Show loss ratios" |

- **Name search** is case-insensitive and supports partial matching.
- **ID search** requires a numeric customer ID.
- **Analytics** converts natural language to SQL — use for aggregate queries, trends, comparisons, or any question the structured tools can't answer directly.

### 2. Present Customer Profile

When displaying customer information, organize it clearly:

**Personal Details**
- Full name, date of birth
- Email, phone
- City, state

**Policy Summary**
- Number of active and total policies
- Policy types (auto, home, travel, life)
- Coverage amounts and premiums

### 3. Present Policy Data

When the user asks for full policy details, use `crm_get_policies` and present each policy:
- Policy ID and type
- Status (active/expired/cancelled/suspended)
- Start and end dates
- Coverage amount and premium amount

### 4. Analytics Queries

Use `crm_analytics` for questions like:
- "What is the total coverage amount by policy type?"
- "Show me all pending claims"
- "Which customer has the highest claim amount?"
- "What is our profit analysis by policy type?"
- "What is the loss ratio for each policy type?"

### Response Guidelines

- Present data clearly using structured formatting.
- If multiple customers match a search, present a summary and ask which one.
- For analytics results, include the relevant numbers and explain insights.
- Note that CRM data reflects the synthetic insurance database — not real customer records.