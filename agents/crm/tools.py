"""CRM tools — read-only access to the local insurance customer JSON data."""

import json
import pathlib
from langchain_core.tools import tool

_DATA_PATH = pathlib.Path(__file__).parent / "data" / "customer-insurance.json"
_CUSTOMERS: list[dict] | None = None


def _load() -> list[dict]:
    global _CUSTOMERS
    if _CUSTOMERS is None:
        _CUSTOMERS = json.loads(_DATA_PATH.read_text())
    return _CUSTOMERS


@tool
def crm_search_name(query: str) -> str:
    """Search insurance customers by name (case-insensitive, partial match).

    Args:
        query: Full or partial customer name to search for.

    Returns:
        JSON array of matching customer profiles (without full policy details).
    """
    q = query.lower()
    results = []
    for c in _load():
        if q in c.get("fullName", "").lower():
            summary = {
                "clientID": c["clientID"],
                "fullName": c["fullName"],
                "dateOfBirth": c.get("dateOfBirth"),
                "nationality": c.get("nationality"),
                "contactDetails": c.get("contactDetails"),
                "address": c.get("address"),
                "activePolicies": sum(
                    1 for p in c.get("policies", []) if p.get("PolicyStatus") == "Active"
                ),
                "totalPolicies": len(c.get("policies", [])),
            }
            results.append(summary)
    if not results:
        return json.dumps({"message": f"No customers found matching '{query}'."})
    return json.dumps(results, indent=2)


@tool
def crm_search_id(query: str) -> str:
    """Look up an insurance customer by their exact client ID.

    Args:
        query: The exact client ID to look up.

    Returns:
        JSON object with full customer profile (without detailed policy bodies).
    """
    for c in _load():
        if c.get("clientID") == query:
            summary = {
                "clientID": c["clientID"],
                "fullName": c["fullName"],
                "dateOfBirth": c.get("dateOfBirth"),
                "nationality": c.get("nationality"),
                "contactDetails": c.get("contactDetails"),
                "address": c.get("address"),
                "policies": [
                    {
                        "PolicyNo": p["PolicyNo"],
                        "ProductType": p["ProductType"],
                        "PolicyStatus": p.get("PolicyStatus"),
                        "EffectiveDate": p.get("EffectiveDate"),
                        "ExpiryDate": p.get("ExpiryDate"),
                    }
                    for p in c.get("policies", [])
                ],
                "claims": [
                    {
                        "ClaimNo": cl["ClaimNo"],
                        "PolicyNo": cl["PolicyNo"],
                        "ClaimStatus": cl.get("ClaimStatus"),
                        "ClaimType": cl.get("ClaimType"),
                    }
                    for cl in c.get("claims", [])
                ],
            }
            return json.dumps(summary, indent=2)
    return json.dumps({"message": f"No customer found with ID '{query}'."})


@tool
def crm_get_policies(query: str) -> str:
    """Get full policy details for a customer by their client ID.

    Args:
        query: The client ID whose policies to retrieve.

    Returns:
        JSON array with complete policy objects including all coverage details.
    """
    for c in _load():
        if c.get("clientID") == query:
            return json.dumps(c.get("policies", []), indent=2)
    return json.dumps({"message": f"No customer found with ID '{query}'."})


@tool
def crm_list_customers() -> str:
    """List all insurance customers with a brief summary of each.

    Returns:
        JSON array of customer summaries.
    """
    results = []
    for c in _load():
        results.append(
            {
                "clientID": c["clientID"],
                "fullName": c["fullName"],
                "city": c.get("address", {}).get("city"),
                "activePolicies": sum(
                    1 for p in c.get("policies", []) if p.get("PolicyStatus") == "Active"
                ),
            }
        )
    return json.dumps(results, indent=2)


CRM_TOOLS = [crm_search_name, crm_search_id, crm_get_policies, crm_list_customers]
