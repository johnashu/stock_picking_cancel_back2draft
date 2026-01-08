{
    "name": "Stock Picking Cancel, Draft & Change Warehouse",
    "summary": "Cancel pickings, reset to draft, and change warehouse with chain preservation",
    "version": "17.0.3.1.6",
    "category": "Warehouse Management",
    "author": "SJR Nebula - John Ashurst",
    "website": "https://sjr.ie",
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "stock",
        "sale_stock",  # Used for testing only.
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/picking_view.xml",
        "views/stock_picking_change_warehouse_views.xml",
    ],
    "images": ["static/description/icon.png"],
}
