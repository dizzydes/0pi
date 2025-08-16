# Minimal MeTTa integration demo
# This shows how to register simple facts and select a service tool using a rule.
# In a real setup, use a proper MeTTa runtime/bindings (e.g., via SingularityNET examples),
# here we stub logic to demonstrate how you'd structure the call site.

from typing import List, Dict

# Toy knowledge base
FACTS: List[Dict] = []


def add_fact(subject: str, predicate: str, obj: str) -> None:
    FACTS.append({"s": subject, "p": predicate, "o": obj})


def find_services_by_category(category: str) -> List[str]:
    """Return service IDs tagged with the given category"""
    ids = []
    for f in FACTS:
        if f["p"] == "category" and f["o"].lower() == category.lower():
            ids.append(f["s"])  # subject is service_id
    return ids


def choose_cheapest(services: List[Dict]) -> Dict:
    if not services:
        return {}
    return min(services, key=lambda r: float(r.get("price_per_call_usdc", 0.0)))


# Example flow to populate facts from your MCP catalog
if __name__ == "__main__":
    import json
    from pathlib import Path
    cat_path = Path(__file__).resolve().parents[1] / "mcp_server" / "catalog.json"
    catalog = json.loads(cat_path.read_text()) if cat_path.exists() else []

    # Insert facts: (service_id, category)
    for row in catalog:
        add_fact(row["id"], "category", row.get("category", ""))

    # Reason over category
    weather_ids = find_services_by_category("weather")
    weather_services = [r for r in catalog if r["id"] in weather_ids]
    chosen = choose_cheapest(weather_services)
    print("Chosen service for category=weather:", chosen.get("id"), chosen.get("name"))

