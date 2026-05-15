#!/usr/bin/env python
"""Synthetic Insurance Data Generator.

Generates synthetic insurance data for customers, policies, and claims
across 4 insurance types (auto, home, travel, life) and stores them in
a DuckDB database.
"""

import argparse
import random
from datetime import datetime, timedelta

import duckdb
from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

# Constants
INSURANCE_TYPES = ["auto", "home", "travel", "life"]
CLAIM_STATUSES = ["pending", "approved", "denied", "processing"]
POLICY_STATUSES = ["active", "expired", "cancelled", "suspended"]

COVERAGE_RANGES = {
    "auto": (10_000, 100_000),
    "home": (100_000, 1_000_000),
    "travel": (1_000, 50_000),
    "life": (50_000, 1_000_000),
}

PREMIUM_RATES = {
    "auto": (0.02, 0.08),
    "home": (0.005, 0.02),
    "travel": (0.1, 0.3),
    "life": (0.01, 0.05),
}

CLAIM_TYPES = {
    "auto": ["collision", "comprehensive", "liability", "theft"],
    "home": ["fire", "theft", "water damage", "storm damage", "vandalism"],
    "travel": ["trip cancellation", "medical emergency", "lost luggage", "flight delay"],
    "life": ["death benefit", "terminal illness", "disability"],
}


def generate_customers(num_customers: int = 1000) -> list[dict]:
    """Generate synthetic customer data."""
    customers = []
    for customer_id in range(1, num_customers + 1):
        customers.append(
            {
                "customer_id": customer_id,
                "first_name": fake.first_name(),
                "last_name": fake.last_name(),
                "email": fake.email(),
                "phone": fake.phone_number(),
                "date_of_birth": fake.date_of_birth(minimum_age=18, maximum_age=85),
                "address": fake.street_address(),
                "city": fake.city(),
                "state": fake.state_abbr(),
                "zip_code": fake.zipcode(),
            }
        )
    return customers


def generate_policies(customers: list[dict], policies_per_customer_range: tuple = (1, 4)) -> list[dict]:
    """Generate synthetic policy data."""
    policies = []
    policy_id = 1

    for customer in customers:
        num_policies = random.randint(*policies_per_customer_range)
        customer_policy_types = random.sample(
            INSURANCE_TYPES, min(num_policies, len(INSURANCE_TYPES))
        )
        for policy_type in customer_policy_types:
            min_cov, max_cov = COVERAGE_RANGES[policy_type]
            coverage_amount = random.randint(min_cov, max_cov)

            min_rate, max_rate = PREMIUM_RATES[policy_type]
            premium_amount = coverage_amount * random.uniform(min_rate, max_rate)

            start_date = fake.date_between(start_date="-2y", end_date="today")
            end_date = start_date + timedelta(days=365)

            policies.append(
                {
                    "policy_id": policy_id,
                    "customer_id": customer["customer_id"],
                    "policy_type": policy_type,
                    "coverage_amount": coverage_amount,
                    "premium_amount": round(premium_amount, 2),
                    "start_date": start_date,
                    "end_date": end_date,
                    "status": random.choice(POLICY_STATUSES),
                }
            )
            policy_id += 1

    return policies


def generate_claims(policies: list[dict], claim_probability: float = 0.3) -> list[dict]:
    """Generate synthetic claims data."""
    claims = []
    claim_id = 1

    for policy in policies:
        if random.random() < claim_probability:
            num_claims = random.choices([1, 2, 3], weights=[70, 25, 5])[0]
            for _ in range(num_claims):
                max_claim = policy["coverage_amount"] * 0.8
                claim_amount = random.uniform(1000, max_claim)

                claim_date = fake.date_between(
                    start_date=policy["start_date"],
                    end_date=min(policy["end_date"], datetime.now().date()),
                )

                claims.append(
                    {
                        "claim_id": claim_id,
                        "policy_id": policy["policy_id"],
                        "claim_amount": round(claim_amount, 2),
                        "claim_date": claim_date,
                        "claim_status": random.choice(CLAIM_STATUSES),
                        "claim_type": random.choice(CLAIM_TYPES[policy["policy_type"]]),
                        "description": fake.text(max_nb_chars=200),
                    }
                )
                claim_id += 1

    return claims


def create_database(db_path: str = "insurance_data.db") -> duckdb.DuckDBPyConnection:
    """Create DuckDB database and tables."""
    conn = duckdb.connect(db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id INTEGER PRIMARY KEY,
            first_name VARCHAR,
            last_name VARCHAR,
            email VARCHAR,
            phone VARCHAR,
            date_of_birth DATE,
            address VARCHAR,
            city VARCHAR,
            state VARCHAR,
            zip_code VARCHAR
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS policies (
            policy_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            policy_type VARCHAR,
            coverage_amount DECIMAL(12,2),
            premium_amount DECIMAL(10,2),
            start_date DATE,
            end_date DATE,
            status VARCHAR,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            claim_id INTEGER PRIMARY KEY,
            policy_id INTEGER,
            claim_amount DECIMAL(12,2),
            claim_date DATE,
            claim_status VARCHAR,
            claim_type VARCHAR,
            description TEXT,
            FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
        )
    """)

    return conn


def insert_data(conn: duckdb.DuckDBPyConnection, customers: list, policies: list, claims: list) -> None:
    """Insert generated data into database."""
    conn.execute("DELETE FROM claims")
    conn.execute("DELETE FROM policies")
    conn.execute("DELETE FROM customers")

    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [tuple(c.values()) for c in customers],
    )
    conn.executemany(
        "INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [tuple(p.values()) for p in policies],
    )
    conn.executemany(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?)",
        [tuple(cl.values()) for cl in claims],
    )
    conn.commit()


def print_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """Print summary statistics of generated data."""
    print("\n=== DATA GENERATION SUMMARY ===")

    customer_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    print(f"Customers generated: {customer_count}")

    policy_counts = conn.execute(
        "SELECT policy_type, COUNT(*) FROM policies GROUP BY policy_type ORDER BY policy_type"
    ).fetchall()
    print("\nPolicies by type:")
    total_policies = 0
    for policy_type, count in policy_counts:
        print(f"  {policy_type}: {count}")
        total_policies += count
    print(f"  Total: {total_policies}")

    claim_counts = conn.execute(
        "SELECT claim_status, COUNT(*) FROM claims GROUP BY claim_status ORDER BY claim_status"
    ).fetchall()
    print("\nClaims by status:")
    total_claims = 0
    for status, count in claim_counts:
        print(f"  {status}: {count}")
        total_claims += count
    print(f"  Total: {total_claims}")

    total_coverage = conn.execute("SELECT SUM(coverage_amount) FROM policies").fetchone()[0]
    total_premiums = conn.execute("SELECT SUM(premium_amount) FROM policies").fetchone()[0]
    total_claims_amount = conn.execute("SELECT SUM(claim_amount) FROM claims").fetchone()[0]

    print("\nFinancial Summary:")
    print(f"  Total Coverage: ${total_coverage:,.2f}")
    print(f"  Total Premiums: ${total_premiums:,.2f}")
    print(f"  Total Claims: ${total_claims_amount:,.2f}")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic insurance data")
    parser.add_argument(
        "--customers", type=int, default=1000, help="Number of customers (default: 1000)"
    )
    parser.add_argument(
        "--db-path", default="insurance_data.db", help="DuckDB database path (default: insurance_data.db)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (default: 42)"
    )
    args = parser.parse_args()

    Faker.seed(args.seed)
    random.seed(args.seed)

    print(f"Generating synthetic insurance data...")
    print(f"  Customers: {args.customers}")
    print(f"  Database:  {args.db_path}")
    print(f"  Seed:      {args.seed}")

    customers = generate_customers(args.customers)
    policies = generate_policies(customers)
    claims = generate_claims(policies)

    conn = create_database(args.db_path)
    insert_data(conn, customers, policies, claims)
    print_summary(conn)
    conn.close()

    print(f"\nDone! Database saved to: {args.db_path}")


if __name__ == "__main__":
    main()
