# Modified from Stock back 2 draft by OCA.
# Original code: https://github.com/OCA/stock-logistics-workflow
{
    "name": "Pickings cancel and back to draft",
    "summary": "Cancel pickings, reopen cancelled pickings, and change warehouse",
    "version": "17.0.2.0.3",
    "category": "Warehouse Management",
    "author": "John Ashurst",
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": ["stock"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/picking_view.xml",
        "views/stock_picking_change_warehouse_views.xml",
    ],
    "images": ["images/picking.png"],
}
