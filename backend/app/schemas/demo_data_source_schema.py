"""
Demo Data Sources Schema

Defines available demo data sources that can be installed with one click.
Add new demo databases here to make them available in the UI.
"""

from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime


class DemoDataSourceDefinition(BaseModel):
    """Definition of a demo data source that can be installed."""
    id: str
    name: str  # Data source (agent) name
    connection_name: Optional[str] = None  # Connection name (defaults to name if not set)
    description: str
    type: str  # e.g., "sqlite", "postgresql"
    config: Dict[str, Any]
    credentials: Dict[str, Any] = {}
    conversation_starters: List[str] = []
    instructions: List[str] = []  # List of instruction texts to create for this data source


class DemoDataSourceListItem(BaseModel):
    """Response schema for listing available demo data sources."""
    id: str
    name: str
    description: str
    type: str
    installed: bool = False
    installed_data_source_id: Optional[str] = None


class DemoDataSourceInstallResponse(BaseModel):
    """Response schema for installing a demo data source."""
    success: bool
    message: str
    data_source_id: Optional[str] = None
    already_installed: bool = False


# Registry of available demo data sources
# Add new demo databases here
# Sample databases offered in "Add Connection". City Mart Retail is our own
# Myanmar retail dataset; the upstream Music Store (chinook) and Financial Market
# (stocks) samples were removed in favour of it.
DEMO_DATA_SOURCES: Dict[str, DemoDataSourceDefinition] = {
    "citymart": DemoDataSourceDefinition(
        id="citymart",
        name="City Mart Retail",
        connection_name="City Mart Retail",
        description="Sample Myanmar retail database (City Mart-style): outlets across banners (City Mart, Marketplace, City Express, Ocean, Seasons Bakery), products & categories, sales with discounts & promotions, City Rewards loyalty members, stock/inventory, and channels — 3 years, MMK. Powered by DuckDB.",
        type="duckdb",
        config={
            "database": "demo-datasources/citymart_retail.duckdb",
        },
        credentials={},
        conversation_starters=[
            "Top 10 products by net sales in the last quarter",
            "Compare net sales by banner and channel this year",
            "City Rewards members vs non-members: average basket value",
            "How much did Thingyan lift daily sales vs a normal day?",
            "Which outlets are understocked on their fast-moving products?",
            "Discount and promotion impact on net sales by department",
        ],
        instructions=[
            "This is a star schema: fact_sales (line items) and fact_inventory / fact_loyalty_txn join to dim_outlet, dim_product, dim_category, dim_customer, dim_channel, dim_date, dim_promotion, dim_supplier. All money is in Myanmar Kyat (MMK).",
            "Net sales = net_amount (already = gross_amount - discount_amount). Use net_amount for revenue unless gross is explicitly asked.",
            "A sale is a City Rewards MEMBER sale when fact_sales.customer_id > 0; customer_id = 0 means a walk-in / non-member. Join dim_customer for tier (Classic/Silver/Gold), city, and points.",
            "Banners live on dim_outlet.banner: City Mart & Marketplace (supermarkets), City Express (convenience), Ocean (hypermarket), Seasons Bakery. Channels on dim_channel: In-store, City Mall Online, City Mart App, Delivery.",
            "Festivals are on dim_date.festival (Thingyan, Thadingyut, Christmas/New Year) and drive large sales spikes — use them for seasonality questions.",
            "For 'top X by Y' queries use a bar chart; for trends over dim_date use a line chart.",
        ],
    ),
}


def get_demo_data_source(demo_id: str) -> Optional[DemoDataSourceDefinition]:
    """Get a demo data source definition by ID."""
    return DEMO_DATA_SOURCES.get(demo_id)


def list_demo_data_sources() -> list[DemoDataSourceDefinition]:
    """List all available demo data sources."""
    return list(DEMO_DATA_SOURCES.values())
